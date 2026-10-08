"""Optional external master-audio alignment using the existing acoustic synchronizer.

Offsets share the camera-sync convention: a sample at source time ``s`` is at
``s + offset_sec`` on the original reference-camera timeline. Nothing in this
module changes the camera overlap or shortens the requested edit range.
"""

import json
import math
import os
import subprocess
import tempfile
from fractions import Fraction

from .audio_sync import SCORE_LOW, sync_single_target


def _probe_stream(media_path, selector, entries):
    command = [
        "ffprobe", "-v", "error", "-select_streams", selector,
        "-show_entries", entries, "-of", "json", os.fspath(media_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        raise ValueError(f"Could not probe {media_path}: {error}") from error


def probe_reference_video(media_path):
    """Read the actual reference frame rate and dimensions, retaining NTSC fractions."""
    data = _probe_stream(
        media_path, "v:0", "stream=width,height,avg_frame_rate,r_frame_rate",
    )
    streams = data.get("streams", [])
    if not streams:
        raise ValueError(f"Reference has no video stream: {media_path}")
    stream = streams[0]
    rate = None
    for field in ("avg_frame_rate", "r_frame_rate"):
        try:
            candidate = Fraction(str(stream.get(field, "0/1")))
            if candidate > 0:
                rate = candidate
                break
        except (ValueError, ZeroDivisionError):
            continue
    try:
        width, height = int(stream["width"]), int(stream["height"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Reference video dimensions are unavailable: {media_path}") from error
    if rate is None or width <= 0 or height <= 0:
        raise ValueError(f"Reference video format is invalid: {media_path}")
    return {
        "fps": float(rate), "fps_num": rate.numerator, "fps_den": rate.denominator,
        "width": width, "height": height,
    }


def _audio_duration(media_path):
    """Require an audio stream; return its duration when known, otherwise None.

    Container duration can describe a video track that outlasts the audio, so it
    is never sufficient evidence that a master covers the requested range.
    """
    data = _probe_stream(
        media_path, "a:0", "stream=duration:stream_tags=DURATION:format=duration",
    )
    streams = data.get("streams", [])
    if not streams:
        raise ValueError(f"Master audio has no audio stream: {media_path}")
    stream = streams[0]
    candidates = [stream.get("duration")]
    tagged_duration = stream.get("tags", {}).get("DURATION")
    if tagged_duration:
        try:
            hours, minutes, seconds = tagged_duration.split(":")
            candidates.append(float(hours) * 3600 + float(minutes) * 60 + float(seconds))
        except (ValueError, AttributeError):
            pass
    for value in candidates:
        try:
            duration = float(value)
            if math.isfinite(duration) and duration > 0:
                return duration
        except (TypeError, ValueError):
            pass
    return None


def align_master_audio(master_path, ref_info, ref_start_sec, ref_end_sec, *,
                       sr=8000, sample_dur=None, full_scan=False, refine_subframe=True):
    """Return verified master metadata, or None when the optional input is omitted.

    An explicitly requested file must align confidently and cover the full camera
    range. A bad input is an error, never an implicit fallback to camera audio.
    """
    if master_path is None:
        return None
    if sr <= 0 or not all(math.isfinite(value) for value in (ref_start_sec, ref_end_sec)) or ref_end_sec <= ref_start_sec:
        raise ValueError("Master alignment requires a positive sample rate and a finite, nonempty reference range")
    path = os.path.abspath(os.path.expanduser(os.fspath(master_path)))
    if not os.path.isfile(path):
        raise ValueError(f"Master audio file not found: {path}")
    duration = _audio_duration(path)
    with tempfile.TemporaryDirectory(prefix="multicam-master-") as directory:
        result = sync_single_target(
            ref_info, path, directory, sr=sr,
            max_dur=sample_dur if duration is not None else None,
            full_scan=full_scan if duration is not None else True,
            refine_subframe=refine_subframe,
        )
    if duration is None:
        try:
            duration = float(result["analyzed_audio_duration_sec"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Master audio duration could not be verified: {path}") from error
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"Master audio duration is invalid: {path}")
    try:
        offset = float(result["offset_sec"])
        score = float(result["peak_z_score"])
        confidence = float(result["confidence"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Master audio alignment returned invalid metadata") from error
    if not all(math.isfinite(value) for value in (offset, score, confidence)):
        raise ValueError("Master audio alignment returned non-finite values")
    if score < SCORE_LOW:
        raise ValueError(
            f"Master audio alignment confidence is too low (score {score:.2f} < {SCORE_LOW:.1f}); "
            "verify shared audible content or retry with --full-scan. Camera audio was not substituted."
        )
    # Allow only numerical/sample rounding, never a missing frame or padded audio.
    tolerance = max(1e-6, 1.0 / sr)
    if offset > ref_start_sec + tolerance or offset + duration < ref_end_sec - tolerance:
        raise ValueError(
            f"Master audio covers reference time {offset:.6f}..{offset + duration:.6f}s, "
            f"but the camera range is {ref_start_sec:.6f}..{ref_end_sec:.6f}s. "
            "Provide a complete master or explicitly choose --ref-start / --ref-end; "
            "the camera range was not shortened."
        )
    return {
        "path": path, "offset_sec": offset, "confidence": confidence,
        "peak_z_score": score, "duration_sec": duration,
    }
