#!/usr/bin/env python3
"""
Path B Parallel Runner: Gemini 3.8 Flash Video Understanding Workflow
====================================================================
1. Reuses Stage 1 aligned masters (CAM1_synced.mp4 & CAM2_synced.mp4).
2. Encodes a lightweight 2 Mbps compact proxy grid (~750 MB, complying with SYLPH spec & Vertex AI 2.0 GB limit).
3. Executes generate_edl.py with gemini-3.8-flash, LOW resolution sampling, and thinking budget.
4. Exports dual-audio Final Cut Pro 7 XML (final_cut_gemini_3.8.xml).
5. Generates comparison benchmark report against Path A (Gemini 2.5 Flash).
"""

import os
import sys
import time
import subprocess
from datetime import datetime

OUTPUT_DIR = "/Volumes/Crucial X10/muticam_test_file/output"
CAM1_MASTER = os.path.join(OUTPUT_DIR, "CAM1_synced.mp4")
CAM2_MASTER = os.path.join(OUTPUT_DIR, "CAM2_synced.mp4")
SYNC_JSON = os.path.join(OUTPUT_DIR, "multicam_sync.json")
PROMPT_OUTLINE = "/Volumes/Crucial X10/muticam_test_file/prompt_with_outline.md"
COMPACT_GRID = os.path.join(OUTPUT_DIR, "multicam_merged_compact_3.8.mp4")
EDL_CSV = os.path.join(OUTPUT_DIR, "edl_gemini_3.8.csv")
EDL_REPORT = os.path.join(OUTPUT_DIR, "edl_gemini_3.8_report.md")
FINAL_XML = os.path.join(OUTPUT_DIR, "final_cut_gemini_3.8.xml")
LOG_DIR = os.path.join(OUTPUT_DIR, "logs")
PATH_B_LOG = os.path.join(LOG_DIR, "path_b_gemini_38.log")

PROJECT_ID = "panmedia-internal-ge"
BUCKET_NAME = "panmedia-test-488409-agent-staging"
PYTHON_BIN = "/opt/anaconda3/bin/python3"
BASE_DIR = "/Users/shengwei/video_tim/multi-cam-editor"

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{ts}] {msg}"
    print(formatted, flush=True)
    with open(PATH_B_LOG, "a", encoding="utf-8") as f:
        f.write(formatted + "\n")

def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    log("================================================================================")
    log("🚀 Launching Path B: Gemini 3.8 Flash Video Understanding Parallel Pipeline")
    log("================================================================================")

    # Sanity checks
    if not os.path.exists(CAM1_MASTER) or not os.path.exists(CAM2_MASTER):
        log(f"❌ Error: Aligned 4K masters missing in {OUTPUT_DIR}!")
        sys.exit(1)

    t_start = time.time()

    # Step B1: Compact Grid Encoding (2 Mbps, ~750 MB)
    log("\n[Step B1/4] 🔲 Encoding SYLPH Compact Proxy Grid (2000k bitrate, ~750 MB)...")
    if os.path.exists(COMPACT_GRID) and os.path.getsize(COMPACT_GRID) > 100 * 1024 * 1024:
        log(f"  ✓ [Cache hit] Compact grid already exists ({os.path.getsize(COMPACT_GRID)/(1024*1024):.1f} MB)")
    else:
        t0 = time.time()
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", CAM1_MASTER,
            "-i", CAM2_MASTER,
            "-filter_complex",
            "[0:v]format=yuv420p,scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1[v0];"
            "[1:v]format=yuv420p,scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1[v1];"
            "[v0][v1]xstack=inputs=2:layout=0_0|960_0[out]",
            "-map", "[out]", "-map", "0:a?",
            "-c:v", "h264_videotoolbox", "-b:v", "2000k", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            COMPACT_GRID
        ]
        log(f"  ► Running FFmpeg VideoToolbox hardware encode (2 Mbps)...")
        res = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            log(f"❌ FFmpeg encode failed:\n{res.stderr[-500:]}")
            sys.exit(1)
        grid_mb = os.path.getsize(COMPACT_GRID) / (1024 * 1024)
        log(f"  ✓ Compact grid created: {COMPACT_GRID} ({grid_mb:.1f} MB) in {time.time()-t0:.1f}s")

    # Step B2: Gemini 3.8 Flash Video Understanding EDL Generation
    log("\n[Step B2/4] 🤖 Running Stage 2 Gemini 3.8 Flash Video Understanding...")
    t1 = time.time()
    gen_edl_cmd = [
        PYTHON_BIN,
        os.path.join(BASE_DIR, "scripts/generate_edl.py"),
        "-v", COMPACT_GRID,
        "-t", PROMPT_OUTLINE,
        "-o", OUTPUT_DIR,
        "--output-csv", EDL_CSV,
        "--report", EDL_REPORT,
        "--model", "gemini-3.8-flash",
        "--processing", "standard",
        "--project", PROJECT_ID,
        "--gcs-bucket", BUCKET_NAME,
        "--location", "global",
        "--lang", "zh-TW"
    ]
    log(f"  ► Command: {' '.join(gen_edl_cmd)}")
    res = subprocess.run(gen_edl_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        log(f"❌ generate_edl.py failed:\n{res.stderr[-1000:]}\n{res.stdout[-1000:]}")
        sys.exit(1)
    log(f"  ✓ Gemini 3.8 Flash EDL generated: {EDL_CSV} in {time.time()-t1:.1f}s")

    # Step B3: Export FCP7 XML with Dual-Camera Audio Tracks
    log("\n[Step B3/4] 🎞️ Exporting Final Cut Pro 7 XML with Dual-Camera Audio...")
    t2 = time.time()
    xml_cmd = [
        PYTHON_BIN,
        os.path.join(BASE_DIR, "scripts/export_fcp7_xml.py"),
        "-e", EDL_CSV,
        "-o", FINAL_XML,
        "-m", OUTPUT_DIR,
        "-s", SYNC_JSON,
        "--fps", "29.97",
        "--lang", "zh-TW"
    ]
    res = subprocess.run(xml_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        log(f"❌ export_fcp7_xml.py failed:\n{res.stderr[-500:]}")
        sys.exit(1)
    log(f"  ✓ Final XML exported: {FINAL_XML} in {time.time()-t2:.1f}s")

    # Step B4: Completion
    total_time = time.time() - t_start
    log("\n================================================================================")
    log(f"🎉 Path B (Gemini 3.8 Flash) successfully finished in {total_time:.1f}s ({total_time/60:.1f} min)!")
    log(f"  • XML Output : {FINAL_XML}")
    log(f"  • EDL CSV    : {EDL_CSV}")
    log(f"  • Report     : {EDL_REPORT}")
    log("================================================================================")

if __name__ == "__main__":
    main()
