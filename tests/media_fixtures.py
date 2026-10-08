"""Small deterministic media fixtures for offline CLI delivery verification."""

import json
from pathlib import Path
import subprocess
import wave

import numpy as np


SAMPLE_RATE = 16000


def run(command, timeout=90, binary=False):
    result = subprocess.run([str(part) for part in command], capture_output=True,
                            text=not binary, timeout=timeout)
    if result.returncode:
        output = result.stderr.decode(errors="replace") if binary else result.stderr
        stdout = result.stdout.decode(errors="replace") if binary else result.stdout
        raise AssertionError(f"Command failed ({result.returncode}): {command}\n{stdout[-3000:]}\n{output[-5000:]}")
    return result.stdout


def signal(duration, seed, sample_rate=SAMPLE_RATE):
    """Non-periodic, speech-band noise with irregular changes in spectral colour."""
    rng = np.random.default_rng(seed)
    count = round(duration * sample_rate)
    sound = np.zeros(count, dtype=np.float64)
    cursor = 0
    while cursor < count:
        length = min(count - cursor, int(rng.integers(sample_rate // 9, sample_rate // 2)))
        block = rng.normal(size=length)
        spectrum = np.fft.rfft(block)
        frequencies = np.fft.rfftfreq(length, 1 / sample_rate)
        low = rng.uniform(120, 850)
        high = rng.uniform(1600, 3400)
        spectrum[(frequencies < low) | (frequencies > high)] = 0
        block = np.fft.irfft(spectrum, n=length)
        block /= max(1e-8, np.std(block))
        edge = min(length // 4, sample_rate // 100)
        envelope = np.ones(length)
        envelope[:edge] = np.linspace(0, 1, edge)
        envelope[-edge:] = np.linspace(1, 0, edge)
        sound[cursor:cursor + length] = block * envelope * rng.uniform(0.07, 0.15)
        cursor += length
    return np.clip(sound, -0.8, 0.8).astype(np.float32)


def write_wav(path, samples, sample_rate=SAMPLE_RATE):
    samples = np.asarray(samples)
    channels = samples.shape[1] if samples.ndim == 2 else 1
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes((np.clip(samples, -0.99, 0.99) * 32767).astype("<i2").tobytes())


def video(path, audio, *, color="red", fps="25", duration=10, audio_codec="aac"):
    run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r={fps}:d={duration}",
         "-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-t", duration,
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", audio_codec, "-b:a", "192k", path])


def trim_video(source, destination, start, duration):
    run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-i", source, "-ss", start, "-t", duration, "-map", "0:v:0", "-map", "0:a:0",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", destination])


def probe(path):
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path]))


def decode_audio(path):
    raw = run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", path,
               "-map", "0:a:0", "-ac", "1", "-ar", SAMPLE_RATE, "-f", "f32le", "pipe:1"], binary=True)
    return np.frombuffer(raw, dtype="<f4").copy()


def frame_rgb(path, seconds):
    raw = run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", seconds,
               "-i", path, "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24",
               "-f", "rawvideo", "pipe:1"], binary=True)
    if len(raw) != 3:
        raise AssertionError(f"Expected one RGB pixel at {seconds}s: {path}")
    return np.frombuffer(raw, dtype=np.uint8).astype(float)


def edit_audio(samples, ranges, offset=0):
    return np.concatenate([samples[round((start + offset) * SAMPLE_RATE):round((end + offset) * SAMPLE_RATE)]
                           for start, end in ranges])


def correlation(first, second):
    count = min(len(first), len(second))
    # AAC/resampler boundaries are not representative of the selected source.
    margin = SAMPLE_RATE // 20
    return float(np.corrcoef(first[margin:count-margin], second[margin:count-margin])[0, 1])


def delivery_fixture(folder, *, fps="25", channels=(2, 2), master=False):
    """Known mapping, trim origin and master offset; no sync algorithm mocked."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    originals = folder / "originals"
    originals.mkdir(exist_ok=True)
    cameras = []
    for index, (name, color, channel_count) in enumerate(zip(
            ("z_reference", "a_guest"), ("red", "blue"), channels), 1):
        left = signal(10, index * 10)
        sound = left if channel_count == 1 else np.column_stack((left, signal(10, index * 10 + 1)))
        wav = originals / f"{name}.wav"
        write_wav(wav, sound)
        raw = originals / f"{name}.mp4"
        video(raw, wav, color=color, fps=fps)
        synced = folder / f"{name}_synced.mp4"
        trim_video(raw, synced, 0.8, 8)
        cameras.append({"camera_id": f"CAM{index}", "camera": str(raw), "is_ref": index == 1,
                        "source_path": str(raw), "synced_path": str(synced), "offset_sec": 0,
                        "confidence": 100, "peak_z_score": 80, "duration_sec": 10})
    master_info = None
    if master:
        master_path = folder / "external_master.wav"
        sound = np.column_stack((signal(10, 91), signal(10, 92)))
        sound = np.pad(sound, ((round(0.4 * SAMPLE_RATE), 0), (0, 0)))
        write_wav(master_path, sound)
        master_info = {"path": str(master_path), "offset_sec": -0.4, "peak_z_score": 80,
                       "confidence": 100, "duration_sec": 10.4}
    fps_num, fps_den = (30000, 1001) if fps == "30000/1001" else (int(fps), 1)
    metadata = {"schema_version": 2, "ref_video": cameras[0]["source_path"], "cameras": cameras,
                "trim": {"ref_start_sec": 0.8, "ref_end_sec": 8.8}, "master_audio": master_info,
                "video_format": {"fps": fps_num / fps_den, "fps_num": fps_num, "fps_den": fps_den,
                                 "width": 320, "height": 180}}
    (folder / "multicam_sync.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    edl = folder / "edl_full.csv"
    edl.write_text("Start_Time,End_Time,Best_Camera,Rule,Reason\n"
                   "00:01.000,00:02.000,CAM1,SPEAKER,first\n"
                   "00:02.000,00:03.000,CAM1,SPEAKER,same camera continuation\n"
                   "00:04.000,00:05.000,CAM2,SPEAKER,after removed second\n"
                   "00:05.000,00:06.000,CAM1,SPEAKER,return\n", encoding="utf-8")
    return metadata, edl
