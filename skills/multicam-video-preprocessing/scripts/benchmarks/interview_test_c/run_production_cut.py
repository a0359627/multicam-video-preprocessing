#!/usr/bin/env python3
"""
Production Orchestrator & Benchmark Suite for Multi-Camera Video Pipeline.
Runs Stage 1 (Audio Sync & Normalization), Stage 2 (Gemini 3.8 Flash EDL),
and Stage 3A (FCP7 XML Timeline Export) with end-to-end timing, strict gate checks,
and complete log preservation for post-mortem review.
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def format_duration(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{int(h)}h {int(m)}m {s:.1f}s"
    elif m > 0:
        return f"{int(m)}m {s:.1f}s"
    else:
        return f"{s:.2f}s"


def run_logged_command(cmd, log_path, stage_name):
    print(f"\n{'='*78}")
    print(f"🚀 [START] {stage_name}")
    print(f"   Command: {' '.join(cmd)}")
    print(f"   Log File: {log_path}")
    print(f"{'='*78}\n", flush=True)

    t0 = time.perf_counter()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    with open(log_path, "w", encoding="utf-8") as log_file:
        log_file.write(f"=== {stage_name} Execution Log ===\n")
        log_file.write(f"Timestamp: {datetime.datetime.now().isoformat()}\n")
        log_file.write(f"Command: {' '.join(cmd)}\n")
        log_file.write("=" * 60 + "\n\n")
        log_file.flush()

        env = os.environ.copy()
        env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
        env["PYTHONUNBUFFERED"] = "1"

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=env,
        )

        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log_file.write(line)
            log_file.flush()

        process.wait()

    elapsed = time.perf_counter() - t0
    retcode = process.returncode

    if retcode != 0:
        print(f"\n❌ [ERROR] {stage_name} FAILED with exit code {retcode} in {format_duration(elapsed)}!", flush=True)
        return False, elapsed, retcode

    print(f"\n✅ [SUCCESS] {stage_name} COMPLETED in {format_duration(elapsed)}!", flush=True)
    return True, elapsed, 0


def main():
    root_dir = Path("/Users/shengwei/video_tim/multi-cam-editor").resolve()
    base_data_dir = Path("/Volumes/Crucial X10/muticam_test_file").resolve()
    output_dir = base_data_dir / "output"
    logs_dir = output_dir / "logs"

    ref_cam = base_data_dir / "A機.MP4"
    tgt_cam = base_data_dir / "B機.MP4"
    prompt_file = base_data_dir / "prompt_with_outline.md"

    output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    overall_t0 = time.perf_counter()
    benchmarks = {}

    print(f"""
==============================================================================
🎬  PRODUCTION MULTI-CAM AI EDITING BENCHMARK PIPELINE
==============================================================================
Project Directory : {root_dir}
Input Directory   : {base_data_dir}
Output Directory  : {output_dir}
Reference CAM1    : {ref_cam.name} ({ref_cam.stat().st_size / (1024**3):.2f} GB)
Target CAM2       : {tgt_cam.name} ({tgt_cam.stat().st_size / (1024**3):.2f} GB)
Prompt Outline    : {prompt_file.name}
Start Timestamp   : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
==============================================================================
""")

    # -------------------------------------------------------------------------
    # STAGE 1: Audio Synchronization, Loudness Normalization, Synced Masters & Grid
    # -------------------------------------------------------------------------
    cam1_synced = output_dir / "CAM1_synced.mp4"
    cam2_synced = output_dir / "CAM2_synced.mp4"
    grid_video = output_dir / "multicam_merged_full.mp4"
    sync_json = output_dir / "multicam_sync.json"

    stage1_cmd = [
        sys.executable,
        str(root_dir / "scripts" / "multicam_pipeline.py"),
        "--ref", str(ref_cam),
        "--targets", str(tgt_cam),
        "--ref-output", str(cam1_synced),
        "--target-outputs", str(cam2_synced),
        "--normalize",
        "--merge",
        "-o", str(output_dir),
        "--encoder", "h264_videotoolbox",
        "--video-bitrate", "12000k",
    ]

    ok1, dur1, code1 = run_logged_command(
        stage1_cmd,
        str(logs_dir / "stage1_sync.log"),
        "Stage 1: Acoustic Synchronization & Master Preprocessing"
    )
    benchmarks["Stage 1 (Sync & Normalization & Grid)"] = dur1

    if not ok1:
        print("[Fatal] Pipeline stopped at Gate 1.")
        sys.exit(code1)

    # Gate 1 Verification
    if not sync_json.exists() or sync_json.stat().st_size == 0:
        print(f"[Gate 1 Error] Missing sync JSON: {sync_json}", file=sys.stderr)
        sys.exit(1)
    if not cam1_synced.exists() or cam1_synced.stat().st_size == 0:
        print(f"[Gate 1 Error] Missing CAM1 master: {cam1_synced}", file=sys.stderr)
        sys.exit(1)
    if not cam2_synced.exists() or cam2_synced.stat().st_size == 0:
        print(f"[Gate 1 Error] Missing CAM2 master: {cam2_synced}", file=sys.stderr)
        sys.exit(1)
    if not grid_video.exists() or grid_video.stat().st_size == 0:
        print(f"[Gate 1 Error] Missing composite grid: {grid_video}", file=sys.stderr)
        sys.exit(1)

    print(" Gate 1 Passed: Sync metadata, synced masters, and grid video verified!")

    # -------------------------------------------------------------------------
    # STAGE 2: Gemini 2.5 Flash Multimodal Video Rough-Cut EDL Generation
    # -------------------------------------------------------------------------
    edl_csv = output_dir / "edl_full.csv"
    edl_report = output_dir / "edl_full_report.md"

    stage2_cmd = [
        sys.executable,
        str(root_dir / "scripts" / "generate_edl.py"),
        "-v", str(grid_video),
        "-t", str(prompt_file),
        "-o", str(output_dir),
        "--model", "gemini-2.5-flash",
        "--processing", "standard",
        "--project", "panmedia-internal-ge",
        "--gcs-bucket", "panmedia-test-488409-agent-staging",
        "--location", "global",
        "--lang", "zh-TW",
    ]

    ok2, dur2, code2 = run_logged_command(
        stage2_cmd,
        str(logs_dir / "stage2_edl.log"),
        "Stage 2: Gemini 2.5 Flash Multimodal Rough-Cut EDL"
    )
    benchmarks["Stage 2 (Gemini 2.5 Flash EDL)"] = dur2

    if not ok2:
        print("[Fatal] Pipeline stopped at Gate 2.")
        sys.exit(code2)

    # Gate 2 Verification
    if not edl_csv.exists() or edl_csv.stat().st_size == 0:
        print(f"[Gate 2 Error] Missing EDL CSV: {edl_csv}", file=sys.stderr)
        sys.exit(1)
    if not edl_report.exists() or edl_report.stat().st_size == 0:
        print(f"[Gate 2 Error] Missing EDL Report: {edl_report}", file=sys.stderr)
        sys.exit(1)

    print(" Gate 2 Passed: EDL CSV decisions and audit report verified!")

    # -------------------------------------------------------------------------
    # STAGE 3A: Export FCP7 XML NLE Timeline
    # -------------------------------------------------------------------------
    xml_output = output_dir / "final_cut_full.xml"

    stage3_cmd = [
        sys.executable,
        str(root_dir / "scripts" / "export_fcp7_xml.py"),
        "-d", str(output_dir),
        "-o", str(xml_output),
        "--fps", "29.97",
        "--width", "3840",
        "--height", "2160",
        "--lang", "zh-TW",
    ]

    ok3, dur3, code3 = run_logged_command(
        stage3_cmd,
        str(logs_dir / "stage3_xml.log"),
        "Stage 3A: Final Cut Pro 7 XML Timeline Export"
    )
    benchmarks["Stage 3A (FCP7 XML Export)"] = dur3

    if not ok3:
        print("[Fatal] Pipeline stopped at Gate 3A.")
        sys.exit(code3)

    # Gate 3A Verification
    if not xml_output.exists() or xml_output.stat().st_size == 0:
        print(f"[Gate 3A Error] Missing Final XML: {xml_output}", file=sys.stderr)
        sys.exit(1)

    print(" Gate 3A Passed: FCP7 XML Timeline successfully exported!")

    overall_elapsed = time.perf_counter() - overall_t0
    benchmarks["Total End-to-End Duration"] = overall_elapsed

    # -------------------------------------------------------------------------
    # Audit Report Generation
    # -------------------------------------------------------------------------
    report_file = output_dir / "BENCHMARK_AND_AUDIT_REPORT.md"
    generate_audit_report(
        report_file=report_file,
        benchmarks=benchmarks,
        sync_json=sync_json,
        edl_csv=edl_csv,
        cam1_master=cam1_synced,
        cam2_master=cam2_synced,
        grid_video=grid_video,
        xml_output=xml_output,
        logs_dir=logs_dir,
    )

    print(f"""
==============================================================================
🎉  BENCHMARK & PRODUCTION PIPELINE FINISHED SUCCESSFULLY!
==============================================================================
Total Execution Time: {format_duration(overall_elapsed)}
XML Deliverable     : {xml_output}
Benchmark Report    : {report_file}
Logs Preserved In   : {logs_dir}
==============================================================================
""")


def generate_audit_report(report_file, benchmarks, sync_json, edl_csv, cam1_master, cam2_master, grid_video, xml_output, logs_dir):
    # Read sync info
    sync_data = {}
    if sync_json.exists():
        with open(sync_json, "r", encoding="utf-8") as f:
            sync_data = json.load(f)

    # Read EDL stats
    edl_rows = []
    cam_counts = {}
    cam_durations = {}
    total_edl_dur = 0.0

    if edl_csv.exists():
        import csv
        with open(edl_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)
            if len(rows) > 1:
                header = [h.strip().lower() for h in rows[0]]
                s_idx = 0
                e_idx = 1
                c_idx = 2
                for i, h in enumerate(header):
                    if "start" in h: s_idx = i
                    elif "end" in h: e_idx = i
                    elif "cam" in h: c_idx = i

                for r in rows[1:]:
                    if len(r) > max(s_idx, e_idx, c_idx):
                        c = r[c_idx].strip()
                        s_str = r[s_idx].strip()
                        e_str = r[e_idx].strip()
                        def parse_sec(t):
                            parts = t.split(":")
                            if len(parts) == 3: return float(parts[0])*3600 + float(parts[1])*60 + float(parts[2])
                            elif len(parts) == 2: return float(parts[0])*60 + float(parts[1])
                            return float(t)
                        try:
                            s_sec = parse_sec(s_str)
                            e_sec = parse_sec(e_str)
                            dur = max(0.0, e_sec - s_sec)
                            cam_counts[c] = cam_counts.get(c, 0) + 1
                            cam_durations[c] = cam_durations.get(c, 0.0) + dur
                            total_edl_dur += dur
                        except Exception:
                            pass

    lines = []
    lines.append("# 多機位 AI 剪輯與時間軸流水線：執行效能與覆盤報告")
    lines.append("")
    lines.append(f"**生成時間**：`{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`  ")
    lines.append(f"**硬體環境**：Apple Silicon (VideoToolbox 硬體加速)  ")
    lines.append(f"**雲端架構**：Google Cloud Vertex AI (`gemini-3.8-flash`) + GCS Staging  ")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 一、 階段耗時與效能指標")
    lines.append("")
    lines.append("| 流水線階段 | 處理項目 | 花費時間 | 狀態 | 產出檔案 |")
    lines.append("| :--- | :--- | :--- | :--- | :--- |")
    lines.append(f"| **Stage 1** | 聲學對齊、EBU R128 音量標準化、雙機母帶導出、網格合成 | {format_duration(benchmarks.get('Stage 1 (Sync & Normalization & Grid)', 0))} | 通過 | `CAM1_synced.mp4`, `CAM2_synced.mp4`, `multicam_merged_full.mp4` |")
    lines.append(f"| **Stage 2** | Vertex AI Gemini 3.8 Flash Agentic Video 粗剪決策 | {format_duration(benchmarks.get('Stage 2 (Gemini 3.8 Flash EDL)', 0))} | 通過 | `edl_full.csv`, `edl_full_report.md` |")
    lines.append(f"| **Stage 3A** | FCP7 XML 時間軸導出 (DaVinci / Premiere 規格) | {format_duration(benchmarks.get('Stage 3A (FCP7 XML Export)', 0))} | 通過 | `final_cut_full.xml` |")
    lines.append(f"| **總計耗時** | **端到端完整流水線** | **{format_duration(benchmarks.get('Total End-to-End Duration', 0))}** | **成功** | **可直接匯入剪輯軟體** |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 二、 聲學對齊與技術參數")
    lines.append("")
    ref_name = sync_data.get("reference", {}).get("basename", "A機.MP4")
    lines.append(f"- **基準機位 (Reference CAM1)**：`{ref_name}`")
    for t in sync_data.get("targets", []):
        tname = t.get("target_basename", "B機.MP4")
        offset = t.get("offset_sec", 0.0)
        score = t.get("peak_z_score", 0.0)
        conf = t.get("confidence", 0.0)
        lines.append(f"- **目標機位 (Target CAM2)**：`{tname}`")
        lines.append(f"  - 時間偏移量 (Offset)：`{offset:+.3f}` 秒 ({offset*1000:+.1f} ms)")
        lines.append(f"  - BBC Z-Score 信賴度評分：`{score:.1f}` (可信度 `{conf:.1f}%`)")
    lines.append("- **音訊音量標準**：EBU R128 (`-14.0 LUFS`, True Peak `-1.5 dBTP`, `linear=true`)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 三、 AI 鏡頭切換決策統計")
    lines.append("")
    lines.append(f"- **總鏡頭切換次數 (Total Cuts)**：`{sum(cam_counts.values())}` 次")
    lines.append(f"- **正片有效時長**：`{format_duration(total_edl_dur)}`")
    lines.append("")
    lines.append("| 機位名稱 | 角色定位 | 鏡頭次數 | 累積畫面時長 | 畫面佔比 |")
    lines.append("| :--- | :--- | :--- | :--- | :--- |")
    for cam in sorted(cam_counts.keys()):
        cnt = cam_counts[cam]
        cdur = cam_durations[cam]
        ratio = (cdur / total_edl_dur * 100) if total_edl_dur > 0 else 0
        role = "來賓 沈伯洋" if "1" in cam else ("主持人 工頭堅" if "2" in cam else "其他")
        lines.append(f"| **{cam}** | {role} | {cnt} 次 | {format_duration(cdur)} | {ratio:.1f}% |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 四、 產出檔案清單與大小")
    lines.append("")
    lines.append(f"- **XML 時間軸檔案**：`{xml_output}` ({xml_output.stat().st_size / 1024:.1f} KB)")
    lines.append(f"- **EDL 決策列表**：`{edl_csv}` ({edl_csv.stat().st_size / 1024:.1f} KB)")
    if cam1_master.exists():
        lines.append(f"- **CAM1 同步母帶**：`{cam1_master}` ({cam1_master.stat().st_size / (1024**3):.2f} GB)")
    if cam2_master.exists():
        lines.append(f"- **CAM2 同步母帶**：`{cam2_master}` ({cam2_master.stat().st_size / (1024**3):.2f} GB)")
    if grid_video.exists():
        lines.append(f"- **雙機對照網格視訊**：`{grid_video}` ({grid_video.stat().st_size / (1024**3):.2f} GB)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 五、 覆盤日誌清單")
    lines.append("")
    for lf in sorted(logs_dir.glob("*.log")):
        lines.append(f"- [`{lf.name}`]({lf.as_uri()}) ({lf.stat().st_size / 1024:.1f} KB)")
    lines.append("")

    with open(report_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"📄 Audit report written to: {report_file}")


if __name__ == "__main__":
    main()
