"""Shared media/time-coordinate handling for the XML and movie deliverables."""

import json
import math
import os
import re
import subprocess
from fractions import Fraction


def probe_media(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    fps = 0.0
    for field in ("avg_frame_rate", "r_frame_rate"):
        try:
            candidate = float(Fraction(video.get(field) or "0/1"))
            if candidate > 0:
                fps = candidate
                break
        except (ValueError, ZeroDivisionError):
            continue
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or audio.get("duration") or 0)
    audio_duration = float(audio.get("duration") or duration)
    tagged = audio.get("tags", {}).get("DURATION")
    if tagged and not audio.get("duration"):
        try:
            h, m, s = tagged.split(":")
            audio_duration = float(h) * 3600 + float(m) * 60 + float(s)
        except (ValueError, TypeError):
            pass
    return {"fps": fps, "width": int(video.get("width", 0)), "height": int(video.get("height", 0)),
            "duration": duration, "channels": int(audio.get("channels", 0)),
            "audio_duration": audio_duration,
            "sample_rate": int(audio.get("sample_rate", 48000))}


def load_sync_metadata(media_dir, sync_json=None):
    path = sync_json or os.path.join(media_dir, "multicam_sync.json")
    if not os.path.isfile(path):
        if sync_json:
            raise FileNotFoundError(path)
        return {}
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)
    data["_directory"] = os.path.dirname(os.path.abspath(path))
    return data


def resolve_path(path, media_dir):
    if not path:
        raise ValueError("Missing media path in synchronization metadata")
    resolved = path if os.path.isabs(path) else os.path.join(media_dir, path)
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"Linked media is missing: {resolved}")
    return os.path.abspath(resolved)


def canonical_camera(name):
    match = re.fullmatch(r"(?:CAM_?|C)(\d+)", str(name).strip(), re.IGNORECASE)
    return "CAM" + str(int(match.group(1))) if match else str(name).strip()


def reference_start(metadata):
    trim = metadata.get("trim") or {}
    if "ref_start_sec" in trim:
        return float(trim["ref_start_sec"])
    value = str(trim.get("ref_start") or "0")
    seconds = 0.0
    for part in value.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def camera_sources(media_dir, metadata=None, raw=False):
    """Map CAM ids by recording order, never by an arbitrary sorted output name."""
    metadata = metadata or {}
    folder = metadata.get("_directory", media_dir)
    entries = metadata.get("cameras") or []
    sources = {}
    if entries:
        for index, entry in enumerate(entries, 1):
            key = entry.get("camera_id") or f"CAM{index}"
            if raw:
                path = entry.get("source_path") or (metadata.get("ref_video") if entry.get("is_ref") else entry.get("camera"))
            else:
                path = entry.get("synced_path")
                if not path:
                    stem, ext = os.path.splitext(os.path.basename(entry["camera"]))
                    path = stem + "_synced" + ext
            path = resolve_path(path, folder)
            info = probe_media(path)
            sources[key] = {"name": key, "path": path, "offset": reference_start(metadata) - float(entry.get("offset_sec", 0)) if raw else 0.0, **info}
        return sources
    if raw:
        raise ValueError("--use-raw-media requires multicam_sync.json")
    files = [f for f in os.listdir(media_dir) if not f.startswith("._") and
             os.path.splitext(f)[1].lower() in (".mp4", ".mov", ".mkv", ".m4v") and
             not any(word in f.lower() for word in ("merged", "final", "seg_"))]
    synced = [f for f in files if "synced" in f.lower()]
    files = sorted(synced or files, key=lambda name: [int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", name)])
    for index, filename in enumerate(files, 1):
        key = f"CAM{index}"
        path = resolve_path(filename, media_dir)
        sources[key] = {"name": key, "path": path, "offset": 0.0, **probe_media(path)}
    if not sources:
        raise ValueError(f"No camera media found in {media_dir}")
    return sources


def audio_sources(cameras, metadata, include_disabled=False):
    """Default to aligned camera mix, or a supplied verified external master."""
    master = metadata.get("master_audio")
    camera_audio = [{**item, "duration": item.get("audio_duration", item["duration"]), "enabled": not bool(master)}
                    for item in cameras.values() if item["channels"] > 0]
    if master:
        path = resolve_path(master["path"], metadata.get("_directory", "."))
        info = probe_media(path)
        if not info["channels"] or not math.isfinite(float(master["offset_sec"])) or float(master.get("peak_z_score", 0)) < 7:
            raise ValueError("External master has no usable audio or verified synchronization; run Stage 1 again")
        selected = [{"name": "Master Mix", "path": path, "offset": reference_start(metadata) - float(master["offset_sec"]),
                     "enabled": True, **info, "duration": min(info["audio_duration"], float(master["duration_sec"]))}]
        if include_disabled:
            selected.extend(camera_audio)
    else:
        selected = camera_audio
    if not selected:
        raise ValueError("No usable audio source; supply camera sound or --master-audio in Stage 1")
    enabled_count = sum(bool(item["enabled"]) for item in selected)
    return [{**item, "gain": 1.0 / enabled_count if item["enabled"] else 1.0} for item in selected]


def validate_source_range(source, start, end, tolerance=0.05):
    begin, finish = start + source["offset"], end + source["offset"]
    if not all(math.isfinite(v) for v in (begin, finish)) or finish <= begin:
        raise ValueError("Invalid media time range")
    if begin < -tolerance or finish > source["duration"] + tolerance:
        raise ValueError(f"{source['name']} does not cover requested interval {begin:.3f}–{finish:.3f}s (duration {source['duration']:.3f}s)")


def continuous_ranges(segments):
    """Join only adjacent source ranges; real EDL deletions stay deleted."""
    ranges = []
    for segment in segments:
        start, end = float(segment["start_sec"]), float(segment["end_sec"])
        if ranges and abs(ranges[-1][1] - start) < 1e-8:
            ranges[-1] = (ranges[-1][0], end)
        else:
            ranges.append((start, end))
    return ranges


def mux_timeline_audio(video_path, output_path, sources, segments, audio_bitrate="192k"):
    """Encode audio once across camera cuts, keeping all selected speakers audible."""
    ranges = continuous_ranges(segments)
    if not ranges:
        raise ValueError("Empty timeline")
    lo, hi = min(r[0] for r in ranges), max(r[1] for r in ranges)
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", video_path]
    filters = []
    for index, source in enumerate(sources, 1):
        for start, end in ranges:
            validate_source_range(source, start, end)
        cmd.extend(["-i", source["path"]])
        start = max(0.0, source["offset"] + lo)
        end = source["offset"] + hi
        filters.append(f"[{index}:a:0]atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[a{index}]")
    inputs = "".join(f"[a{i}]" for i in range(1, len(sources) + 1))
    filters.append(f"{inputs}amix=inputs={len(sources)}:duration=shortest:dropout_transition=0:normalize=1[mix]")
    if len(ranges) == 1:
        filters.append("[mix]anull[aout]")
    else:
        filters.append(f"[mix]asplit={len(ranges)}" + "".join(f"[slice{i}]" for i in range(len(ranges))))
        for index, (start, end) in enumerate(ranges):
            filters.append(f"[slice{index}]atrim=start={start-lo:.9f}:end={end-lo:.9f},asetpts=PTS-STARTPTS[part{index}]")
        filters.append("".join(f"[part{i}]" for i in range(len(ranges))) + f"concat=n={len(ranges)}:v=0:a=1[aout]")
    cmd.extend(["-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", audio_bitrate, "-movflags", "+faststart", output_path])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Timeline audio assembly failed: {result.stderr[-1500:]}")
