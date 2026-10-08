"""
Multi-camera reporting and data export module (Reporter Module).
Prints formatted terminal matrices and exports JSON and CSV alignment metadata.
"""

import csv
import json
import os
from .time_utils import format_seconds


def print_sync_table(ref_info, target_results, trim_info=None):
    """
    Print multi-camera time alignment matrix to the terminal.
    """
    total_cams = len(target_results) + 1
    print("-" * 78)
    print(f"Reference Camera (Anchor): {ref_info['basename']} (Duration: {format_seconds(ref_info['duration_sec'])})")
    if trim_info and trim_info.get("start") is not None:
        t_s = trim_info["start"]
        t_e = trim_info["end"]
        dur = t_e - t_s
        print(f"Trim Range: {format_seconds(t_s)} → {format_seconds(t_e)} (Duration: {format_seconds(dur)})")
    print("-" * 78)
    status_header = "Trim Start → End" if (trim_info and trim_info.get("start") is not None) else "Status"
    print(f"{'Camera Name':<22} | {'Offset (Delta t)':<18} | {'Conf':<8} | {status_header}")
    print("-" * 78)

    # Reference
    if trim_info and trim_info.get("start") is not None:
        ref_status = f"{format_seconds(trim_info['start'])} → {format_seconds(trim_info['end'])}"
    else:
        ref_status = "Anchor (Reference)"
    print(f"{ref_info['basename'][:22]:<22} | {'0.000000s (Anchor)':<18} | {'100%':<8} | {ref_status}")

    # Targets
    for r in target_results:
        off = r["offset_sec"]
        conf = r["confidence"]
        dir_str = "Late" if off > 0 else ("Early" if off < 0 else "Sync")
        off_display = f"{off:+.3f}s ({dir_str})"

        if conf >= 85:
            conf_display = f"✓ {conf:.1f}%"
        elif conf >= 65:
            conf_display = f"ℹ {conf:.1f}%"
        else:
            conf_display = f"⚠️ {conf:.1f}%"

        if trim_info and trim_info.get("start") is not None:
            t_tgt_start = trim_info["start"] - off
            t_tgt_end = trim_info["end"] - off
            status_display = f"{format_seconds(t_tgt_start)} → {format_seconds(t_tgt_end)}"
        else:
            status_display = "Aligned"

        print(f"{r['target_basename'][:22]:<22} | {off_display:<18} | {conf_display:<8} | {status_display}")
    print("-" * 78)


def export_sync_json(filepath, ref_info, target_results, trim_info=None,
                     master_audio=None, video_format=None):
    """
    Export structured JSON metadata.
    """
    cameras_meta = [{
        "camera": ref_info["basename"],
        "camera_id": "CAM1",
        "source_path": os.path.abspath(ref_info["path"]),
        "synced_path": ref_info.get("synced_path"),
        "is_ref": True,
        "offset_sec": 0.0,
        "confidence": 100.0,
        "duration_sec": ref_info["duration_sec"]
    }]

    for camera_number, r in enumerate(target_results, start=2):
        cameras_meta.append({
            "camera": r["target_basename"],
            "camera_id": f"CAM{camera_number}",
            "source_path": os.path.abspath(r["target_video"]),
            "synced_path": r.get("synced_path"),
            "is_ref": False,
            "offset_sec": r["offset_sec"],
            "confidence": r["confidence"],
            "duration_sec": r["duration_sec"]
        })

    data = {
        "schema_version": 2,
        "ref_video": os.path.abspath(ref_info["path"]),
        "cameras": cameras_meta,
        "master_audio": master_audio,
        "video_format": video_format,
    }

    if trim_info:
        data["trim"] = {
            "ref_start": trim_info.get("start_str"),
            "ref_end": trim_info.get("end_str"),
            "ref_start_sec": float(trim_info["start"]),
            "ref_end_sec": float(trim_info["end"]),
        }

    with open(filepath, "w", encoding="utf-8") as jf:
        json.dump(data, jf, ensure_ascii=False, indent=2)


def export_sync_csv(filepath, ref_info, target_results, trim_info=None):
    """
    Export CSV alignment table.
    """
    with open(filepath, "w", encoding="utf-8", newline="") as cf:
        writer = csv.writer(cf)
        writer.writerow(["Camera", "Is_Reference", "Offset_Seconds", "Confidence_Percent", "Trim_Start", "Trim_End"])

        # Ref
        t_start_s = trim_info.get("start_str") if trim_info else ""
        t_end_s = trim_info.get("end_str") if trim_info else ""
        writer.writerow([ref_info["basename"], "TRUE", "0.000000", "100.0", t_start_s, t_end_s])

        for r in target_results:
            off = r["offset_sec"]
            if trim_info and trim_info.get("start") is not None:
                tgt_s = format_seconds(trim_info["start"] - off)
                tgt_e = format_seconds(trim_info["end"] - off)
            else:
                tgt_s, tgt_e = "", ""

            writer.writerow([r["target_basename"], "FALSE", f"{off:+.6f}", f"{r['confidence']:.1f}", tgt_s, tgt_e])
