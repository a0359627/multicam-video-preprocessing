#!/usr/bin/env python3
"""
FCP7 XML Exporter CLI Tool (export_fcp7_xml.py).
Converts multi-camera EDL CSV files (e.g. edl_full.csv) into a seamless Final Cut Pro 7 XML (xmeml version 4)
timeline file for professional NLEs (DaVinci Resolve, Premiere Pro, Final Cut Pro).

Features:
  - Full Timeline Continuity: Translates agentic video EDL decisions into frame-accurate NLE timelines.
  - Dual Media Reference Modes:
      1. Synchronized Camera Masters (Default): References aligned camera master files (*_synced.mp4).
      2. Raw Original Camera Linking (--use-raw-media): Uses multicam_sync.json global sync offsets to link directly to full un-sliced camera originals.
  - Rich Timeline Markers: Color-coded markers for editing rules ([強制] -> Red, [一般] -> Blue) with full reason comments.
  - Multi-Track Audio Mapping: Continuous camera mix or an automatically aligned optional master.
  - Safe URI Path Encoding: Robust path cleaning and URL-encoding (file://localhost/...) for cross-platform NLE relinking.

Usage Examples:
  # Example 1: Auto-discover full EDL in directory and export XML
  python3 scripts/export_fcp7_xml.py -d ./output/ -o ./output/final_cut_full.xml

  # Example 2: Export specific EDL CSV file
  python3 scripts/export_fcp7_xml.py \
    -e edl_full.csv \
    -o final_cut_full.xml
"""

import argparse
import csv
import glob
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET

# Support internal modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from modules.edl_validator import validate_edl_file, format_validation_report
except ImportError:
    from scripts.modules.edl_validator import validate_edl_file, format_validation_report


from modules.timeline_media import (load_sync_metadata, camera_sources, audio_sources,
                                    canonical_camera, validate_source_range)

DEFAULT_FPS = 30
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
AUDIO_SAMPLE_RATE = 48000
AUDIO_DEPTH = 16


def resolve_fps_characteristics(fps):
    """
    Map float fps to FCP7 integer timebase and ntsc boolean flag according to Apple FCP7 XML specifications.
    Supports standard NTSC fractional frame rates (23.976, 29.97, 59.94) and broadcast film rates.
    """
    fps_f = float(fps)
    KNOWN_RATES = [
        (23.976, 24, True),
        (23.98,  24, True),
        (24.0,   24, False),
        (25.0,   25, False),
        (29.97,  30, True),
        (30.0,   30, False),
        (50.0,   50, False),
        (59.94,  60, True),
        (60.0,   60, False),
    ]
    for target, tb, is_ntsc in KNOWN_RATES:
        if abs(fps_f - target) < 0.01:
            return tb, is_ntsc

    tb = int(round(fps_f))
    sys.stderr.write(
        f"[Warning] Unrecognised frame rate {fps}. Setting FCP7 XML timebase={tb}, ntsc=FALSE.\n"
    )
    sys.stderr.flush()
    return tb, False


def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", str(s))]


def time_str_to_frames(time_str, fps=DEFAULT_FPS):
    if not time_str:
        return 0
    try:
        t_str = str(time_str).strip().replace('"', "").replace("'", "")
        if not t_str:
            return 0
        if ":" in t_str:
            parts = t_str.split(":")
            if len(parts) == 3:
                h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
                sec = h * 3600.0 + m * 60.0 + s
            elif len(parts) == 2:
                m, s = float(parts[0]), float(parts[1])
                sec = m * 60.0 + s
            else:
                sec = float(parts[0])
        else:
            sec = float(t_str)
        return int(round(sec * fps))
    except Exception as e:
        print(f"[Warning] Failed to parse timecode '{time_str}': {e}", file=sys.stderr)
        return 0


def format_path_for_xml(system_path):
    """Convert absolute path to URL-encoded file://localhost URI format for XML."""
    path_obj = Path(system_path)
    path_str = path_obj.as_posix()
    if not path_str.startswith("/"):
        path_str = "/" + path_str
    encoded_path = urllib.parse.quote(path_str, safe="/:")
    return f"file://localhost{encoded_path}"


def auto_discover_camera_files(media_dir):
    """
    Discover camera files in media_dir.
    Prioritizes full synchronized master files (*_synced.mp4), followed by camera video files.
    """
    if not media_dir or not os.path.exists(media_dir):
        return {}
    mapping = {}
    all_files = sorted(os.listdir(media_dir))
    candidates = []

    # 1. First priority: full synchronized camera master files (*_synced.mp4)
    synced_files = [
        os.path.join(media_dir, f) for f in all_files
        if not f.startswith(".") and f.lower().endswith((".mp4", ".mov", ".mkv", ".m4v"))
        and "synced" in f.lower() and "merged" not in f.lower() and "final" not in f.lower()
    ]
    if synced_files:
        candidates = synced_files

    # 2. Fallback: any valid camera video files
    if not candidates:
        for f in all_files:
            if not f.startswith(".") and any(f.lower().endswith(ext) for ext in (".mp4", ".mov", ".mkv", ".m4v")):
                if "merged" not in f.lower() and "final" not in f.lower() and "seg_" not in f.lower():
                    candidates.append(os.path.join(media_dir, f))

    candidates.sort(key=natural_sort_key)
    for idx, fpath in enumerate(candidates, start=1):
        mapping[f"CAM{idx}"] = fpath
        mapping[f"cam{idx}"] = fpath
        mapping[f"CAM_{idx}"] = fpath
        mapping[f"C{idx}"] = fpath
    return mapping


def load_edl_csv_records(csv_path):
    """Load and parse an EDL CSV file."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"EDL CSV file not found: {csv_path}")

    records = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        delimiter = "\t" if "\t" in sample and "," not in sample else ","
        reader = csv.reader(f, delimiter=delimiter)
        rows = list(reader)

    if not rows:
        return records

    header_row_idx = 0
    for r_idx, row in enumerate(rows[:5]):
        row_str = " ".join(row).lower()
        if any(kw in row_str for kw in ("start", "best_camera", "camera", "cam")):
            header_row_idx = r_idx
            break

    header = [h.strip().lower() for h in rows[header_row_idx]]
    start_idx = next((i for i, h in enumerate(header) if any(kw in h for kw in ("start", "in", "from"))), 0)
    end_idx = next((i for i, h in enumerate(header) if any(kw in h for kw in ("end", "out", "to"))), 1)
    cam_idx = next((i for i, h in enumerate(header) if any(kw in h for kw in ("cam", "source", "clip"))), 2)
    rule_idx = next((i for i, h in enumerate(header) if any(kw in h for kw in ("規則", "rule"))), -1)
    reason_idx = next((i for i, h in enumerate(header) if any(kw in h for kw in ("原因", "reason", "note", "desc"))), -1)

    for row in rows[header_row_idx + 1:]:
        if not row or len(row) < 3:
            continue
        start_str = row[start_idx].strip() if len(row) > start_idx else ""
        end_str = row[end_idx].strip() if len(row) > end_idx else ""
        cam_str = row[cam_idx].strip() if len(row) > cam_idx else ""
        if not start_str or not end_str or not cam_str:
            continue

        rule_str = row[rule_idx].strip() if rule_idx >= 0 and len(row) > rule_idx else ""
        reason_str = row[reason_idx].strip() if reason_idx >= 0 and len(row) > reason_idx else ""

        records.append({
            "start_str": start_str,
            "end_str": end_str,
            "camera": cam_str,
            "rule": rule_str,
            "reason": reason_str
        })
    return records


def create_file_node(file_id, filename, file_url, timebase, duration, width, height, drop_frame=False):
    """Generate standard FCP7 XML <file> node with Reel name matching reference implementation."""
    reel_name = os.path.splitext(filename)[0]
    display_format = "DF" if drop_frame else "NDF"
    return f"""
                    <file id="{file_id}">
                        <name>{filename}</name>
                        <pathurl>{file_url}</pathurl>
                        <rate><timebase>{timebase}</timebase></rate>
                        <duration>{duration}</duration>
                        <timecode>
                            <rate><timebase>{timebase}</timebase></rate>
                            <string>00:00:00:00</string>
                            <frame>0</frame>
                            <displayformat>{display_format}</displayformat>
                            <reel>
                                <name>{reel_name}</name>
                            </reel>
                        </timecode>
                        <media>
                            <video>
                                <samplecharacteristics>
                                    <rate><timebase>{timebase}</timebase></rate>
                                    <width>{width}</width>
                                    <height>{height}</height>
                                    <pixelaspectratio>square</pixelaspectratio>
                                </samplecharacteristics>
                            </video>
                            <audio>
                                <samplecharacteristics>
                                    <depth>{AUDIO_DEPTH}</depth>
                                    <samplerate>{AUDIO_SAMPLE_RATE}</samplerate>
                                </samplecharacteristics>
                                <channelcount>2</channelcount>
                            </audio>
                        </media>
                    </file>"""


def build_fcp7_xml_sequence(all_part_clips, part_audio_list=None, seq_name="final_cut_full",
                            fps=DEFAULT_FPS, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
                            drop_frame=False):
    """Write true source metadata and editable, level-matched audio tracks."""
    root = ET.Element("xmeml", version="4")
    sequence = ET.SubElement(root, "sequence", id="sequence-1")
    def add(parent, tag, value):
        ET.SubElement(parent, tag).text = str(value)
    def rate(parent, value=fps):
        tb, ntsc = resolve_fps_characteristics(value)
        node = ET.SubElement(parent, "rate")
        add(node, "timebase", tb)
        add(node, "ntsc", "TRUE" if ntsc else "FALSE")
    duration = all_part_clips[-1]["timeline_end"]
    add(sequence, "name", seq_name)
    add(sequence, "duration", duration)
    rate(sequence)
    tc = ET.SubElement(sequence, "timecode")
    rate(tc)
    add(tc, "string", "00:00:00:00")
    add(tc, "frame", 0)
    add(tc, "displayformat", "DF" if drop_frame else "NDF")
    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    sample = ET.SubElement(ET.SubElement(video, "format"), "samplecharacteristics")
    rate(sample)
    add(sample, "width", width)
    add(sample, "height", height)
    add(sample, "pixelaspectratio", "square")
    files = {}
    def file_node(parent, path, info, video_media):
        if path in files:
            ET.SubElement(parent, "file", id=files[path])
            return
        file_id = "file-" + str(len(files) + 1)
        files[path] = file_id
        node = ET.SubElement(parent, "file", id=file_id)
        add(node, "name", os.path.basename(path))
        add(node, "pathurl", Path(path).resolve().as_uri())
        source_fps = (info.get("fps") or fps) if video_media else fps
        rate(node, source_fps)
        add(node, "duration", round(info["duration"] * source_fps))
        source_tc = ET.SubElement(node, "timecode")
        rate(source_tc, source_fps)
        add(source_tc, "string", "00:00:00:00")
        add(source_tc, "frame", 0)
        add(source_tc, "displayformat", "NDF")
        source_media = ET.SubElement(node, "media")
        if video_media:
            sample = ET.SubElement(ET.SubElement(source_media, "video"), "samplecharacteristics")
            rate(sample, info.get("fps") or fps)
            add(sample, "width", info.get("width") or width)
            add(sample, "height", info.get("height") or height)
            add(sample, "pixelaspectratio", "square")
        if info.get("channels"):
            source_audio = ET.SubElement(source_media, "audio")
            sample = ET.SubElement(source_audio, "samplecharacteristics")
            add(sample, "depth", AUDIO_DEPTH)
            add(sample, "samplerate", info.get("sample_rate") or AUDIO_SAMPLE_RATE)
            add(source_audio, "channelcount", info["channels"])
            if info["channels"] in (1, 2):
                add(source_audio, "layout", "mono" if info["channels"] == 1 else "stereo")
    track = ET.SubElement(video, "track")
    for index, clip in enumerate(all_part_clips, 1):
        node = ET.SubElement(track, "clipitem", id=f"video-{index}")
        add(node, "name", clip["camera"])
        add(node, "enabled", "TRUE")
        add(node, "duration", clip["timeline_end"] - clip["timeline_start"])
        rate(node)
        for tag, key in (("start", "timeline_start"), ("end", "timeline_end"), ("in", "source_in"), ("out", "source_out")):
            add(node, tag, clip[key])
        file_node(node, clip["file_path"], clip["media_info"], True)
        marker = ET.SubElement(node, "marker")
        add(marker, "name", clip.get("rule", ""))
        add(marker, "comment", clip.get("reason", ""))
        add(marker, "in", clip["source_in"])
        add(marker, "out", clip["source_in"] + 1)
    audio = ET.SubElement(media, "audio")
    add(audio, "channelcount", 2)
    outputs = ET.SubElement(audio, "outputs")
    group = ET.SubElement(outputs, "group")
    add(group, "index", 1)
    add(group, "numchannels", 2)
    add(group, "downmix", 0)
    for output_channel in (1, 2):
        add(ET.SubElement(group, "channel"), "index", output_channel)
    track_index = 0
    for source in part_audio_list or []:
        for channel in range(1, source["channels"] + 1):
            track_index += 1
            track = ET.SubElement(audio, "track")
            add(track, "enabled", "TRUE" if source["enabled"] else "FALSE")
            if source["channels"] == 2:
                add(track, "outputchannelindex", channel)
            for index, span in enumerate(source["spans"], 1):
                node = ET.SubElement(track, "clipitem", id=f"audio-{track_index}-{index}")
                add(node, "name", f"{source['name']} ch{channel}")
                add(node, "enabled", "TRUE" if source["enabled"] else "FALSE")
                add(node, "duration", span["end"] - span["start"])
                rate(node)
                for tag in ("start", "end", "in", "out"):
                    add(node, tag, span[tag])
                # A recorder container may have a different video frame rate.
                # Its audio uses sequence-frame coordinates, never recorder video frames.
                file_node(node, source["path"], source,
                          source["name"] != "Master Mix" and bool(source.get("fps")))
                st = ET.SubElement(node, "sourcetrack")
                add(st, "mediatype", "audio")
                add(st, "trackindex", channel)
                effect = ET.SubElement(ET.SubElement(node, "filter"), "effect")
                add(effect, "name", "Audio Levels")
                add(effect, "effectid", "audiolevels")
                add(effect, "effectcategory", "audio")
                add(effect, "effecttype", "audio")
                parameter = ET.SubElement(effect, "parameter")
                add(parameter, "parameterid", "level")
                add(parameter, "name", "Level")
                add(parameter, "value", source["gain"])
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="unicode") + "\n"


def export_fcp7_xml_pipeline(edl_files, output_path=None, media_dir=None, sync_json=None,
                             fps=None, width=None, height=None,
                             use_raw_media=False, drop_frame=False, strict_edl=False,
                             lang="en"):
    if not edl_files:
        raise ValueError("No EDL CSV files provided for XML export")
    edl_files = sorted(edl_files, key=natural_sort_key)
    media_dir = media_dir or os.path.dirname(os.path.abspath(edl_files[0]))
    output_path = output_path or os.path.join(media_dir, "final_cut_full.xml")
    metadata = load_sync_metadata(media_dir, sync_json)
    cameras = camera_sources(media_dir, metadata, raw=use_raw_media)
    first = next(iter(cameras.values()))
    fps = float(fps or first["fps"])
    width, height = int(width or first["width"]), int(height or first["height"])
    if fps <= 0 or width <= 0 or height <= 0:
        raise ValueError("Cannot determine video frame rate or dimensions")
    if drop_frame and not resolve_fps_characteristics(fps)[1]:
        raise ValueError("--drop-frame requires an NTSC frame rate")
    sounds = audio_sources(cameras, metadata, include_disabled=True)
    for sound in sounds:
        sound["spans"] = []
    clips, cursor = [], 0
    for edl_path in edl_files:
        validation = validate_edl_file(edl_path, known_cameras=list(cameras), lang=lang)
        print(format_validation_report(validation, lang=lang))
        if validation.has_error and strict_edl:
            raise ValueError("EDL validation failed under --strict-edl")
        records = load_edl_csv_records(edl_path)
        if not records:
            raise ValueError(f"No valid EDL rows: {edl_path}")
        for record in records:
            key = canonical_camera(record["camera"])
            if key not in cameras:
                raise ValueError(f"Unknown camera: {record['camera']}")
            source = cameras[key]
            if abs(source["fps"] - fps) > 0.01:
                raise ValueError("Mixed camera/sequence frame rates require conforming before export")
            start = time_str_to_frames(record["start_str"], fps)
            end = time_str_to_frames(record["end_str"], fps)
            length = end - start
            if length <= 0:
                raise ValueError("EDL cut is shorter than one frame")
            validate_source_range(source, start / fps, end / fps, tolerance=1 / fps)
            source_in = round((start / fps + source["offset"]) * fps)
            clip = {"camera": key, "file_path": source["path"], "media_info": source,
                    "source_in": source_in, "source_out": source_in + length,
                    "timeline_start": cursor, "timeline_end": cursor + length,
                    "rule": record["rule"], "reason": record["reason"]}
            if clips and clips[-1]["camera"] == key and clips[-1]["source_out"] == source_in:
                clips[-1]["timeline_end"] += length
                clips[-1]["source_out"] += length
                clips[-1]["reason"] += " / " + record["reason"]
            else:
                clips.append(clip)
            for sound in sounds:
                validate_source_range(sound, start / fps, end / fps, tolerance=1 / fps)
                audio_in = max(0, round((start / fps + sound["offset"]) * fps))
                span = {"start": cursor, "end": cursor + length, "in": audio_in, "out": audio_in + length}
                spans = sound["spans"]
                if spans and spans[-1]["out"] == audio_in and spans[-1]["end"] == cursor:
                    spans[-1]["end"] += length
                    spans[-1]["out"] += length
                else:
                    spans.append(span)
            cursor += length
    xml = build_fcp7_xml_sequence(clips, sounds, os.path.splitext(os.path.basename(output_path))[0], fps, width, height, drop_frame)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as stream:
        stream.write(xml)
    print(f"Exported {output_path}: {len(clips)} video clips, {sum(s['channels'] for s in sounds)} audio tracks, {cursor / fps:.3f}s")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="FCP7 XML Exporter CLI: Convert multi-camera EDL CSV files into Final Cut Pro 7 XML for DaVinci Resolve / Premiere Pro.",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("-e", "--edl", nargs="+", default=None, help="One or more EDL CSV files (e.g. edl_full.csv)")
    parser.add_argument("-d", "--dir", default=None, help="Directory containing EDL CSV files (auto-discovers edl_full.csv)")
    parser.add_argument("-o", "--output", default=None, help="Path to output FCP7 XML file (default: final_cut_full.xml)")
    parser.add_argument("-m", "--media-dir", default=None, help="Directory containing camera media files (defaults to EDL directory)")
    parser.add_argument("-s", "--sync-json", default=None, help="Path to multicam_sync.json for raw camera offset resolution")
    parser.add_argument("--use-raw-media", action="store_true", help="Link to original raw camera footage instead of synchronized camera masters")
    parser.add_argument("--strict-edl", action="store_true",
                        help="EDL 驗證出現 ERROR 時中斷執行（預設僅警告並繼續）")
    parser.add_argument("--lang", default="en",
                        help="Language for the EDL validation report (default: en)")

    parser.add_argument("--fps", type=float, default=None, help="Sequence frame rate (default: detected from media)")
    parser.add_argument("--drop-frame", action="store_true", help="Enable drop-frame timecode (DF) for NTSC sequences (default: NDF)")
    parser.add_argument("--width", type=int, default=None, help="Sequence width (default: detected from media)")
    parser.add_argument("--height", type=int, default=None, help="Sequence height (default: detected from media)")

    args = parser.parse_args()

    edl_files = []
    if args.edl:
        for item in args.edl:
            expanded = glob.glob(item)
            if expanded:
                edl_files.extend(expanded)
            elif os.path.exists(item):
                edl_files.append(item)
    elif args.dir and os.path.exists(args.dir):
        pat = os.path.join(args.dir, "**/edl_*.csv")
        edl_files = glob.glob(pat, recursive=True)
        if not edl_files:
            pat_fallback = os.path.join(args.dir, "**/*.csv")
            edl_files = [f for f in glob.glob(pat_fallback, recursive=True) if "sync" not in os.path.basename(f).lower()]

        # If a unified full-length EDL exists, prioritize it
        if edl_files:
            full_edls = [f for f in edl_files if "full" in os.path.basename(f).lower()]
            if full_edls:
                full_edls.sort(key=lambda x: (0 if os.path.basename(x) == "edl_full.csv" else 1, len(os.path.basename(x))))
                edl_files = [full_edls[0]]

    if not edl_files:
        print("[Error] No EDL CSV files found. Please specify -e/--edl or -d/--dir.", file=sys.stderr)
        sys.exit(1)

    try:
        export_fcp7_xml_pipeline(
            edl_files=edl_files,
            output_path=args.output,
            media_dir=args.media_dir,
            sync_json=args.sync_json,
            fps=args.fps,
            width=args.width,
            height=args.height,
            use_raw_media=args.use_raw_media,
            drop_frame=args.drop_frame,
            strict_edl=args.strict_edl,
            lang=args.lang
        )
    except Exception as e:
        print(f"\n[Error] FCP7 XML export failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
