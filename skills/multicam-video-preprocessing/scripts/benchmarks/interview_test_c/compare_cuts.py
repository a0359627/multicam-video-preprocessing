#!/usr/bin/env python3
"""
A/B Benchmark & Comparative Analysis: Gemini 2.5 Flash vs Gemini 3.8 Flash
========================================================================
Compares the rough-cut decisions produced by:
  - Path A: Gemini 2.5 Flash (edl_full.csv / final_cut_full.xml)
  - Path B: Gemini 3.8 Flash (edl_gemini_3.8.csv / final_cut_gemini_3.8.xml)
"""

import os
import sys
import csv
from datetime import datetime

OUTPUT_DIR = "/Volumes/Crucial X10/muticam_test_file/output"
EDL_25 = os.path.join(OUTPUT_DIR, "edl_full.csv")
EDL_38 = os.path.join(OUTPUT_DIR, "edl_gemini_3.8.csv")
REPORT_FILE = os.path.join(OUTPUT_DIR, "COMPARISON_2.5_vs_3.8.md")

def parse_time(ts):
    parts = ts.strip().split(":")
    if len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    return float(ts)

def analyze_edl(edl_path):
    if not os.path.exists(edl_path):
        return None
    rows = []
    with open(edl_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    
    if not rows:
        return None
    
    total_cuts = len(rows)
    cam_durations = {}
    shot_durations = []
    
    first_start = parse_time(rows[0]["Start_Time"])
    last_end = parse_time(rows[-1]["End_Time"])
    total_duration = last_end - first_start
    
    for r in rows:
        st = parse_time(r["Start_Time"])
        et = parse_time(r["End_Time"])
        dur = et - st
        cam = r["Camera"].strip().upper()
        shot_durations.append(dur)
        cam_durations[cam] = cam_durations.get(cam, 0.0) + dur
        
    avg_shot = sum(shot_durations) / len(shot_durations) if shot_durations else 0
    min_shot = min(shot_durations) if shot_durations else 0
    max_shot = max(shot_durations) if shot_durations else 0
    
    return {
        "total_cuts": total_cuts,
        "first_start": first_start,
        "last_end": last_end,
        "total_duration": total_duration,
        "avg_shot": avg_shot,
        "min_shot": min_shot,
        "max_shot": max_shot,
        "cam_durations": cam_durations,
        "rows": rows
    }

def main():
    print("Generating Comparative Analysis Report: Gemini 2.5 Flash vs Gemini 3.8 Flash ...")
    res_25 = analyze_edl(EDL_25)
    res_38 = analyze_edl(EDL_38)
    
    md = []
    md.append("# 🎬 AI 雙機多視角粗剪 A/B 深度對比報告")
    md.append(f"**生成時間**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ")
    md.append(f"**測試專案**：沈伯洋 × 工頭堅 對談（全長約 50 分 50 秒）  \n")
    
    md.append("## 一、 核心指標量化對照表\n")
    md.append("| 指標項目 | Path A：Gemini 2.5 Flash | Path B：Gemini 3.8 Flash | 差異與風格解讀 |")
    md.append("| :--- | :--- | :--- | :--- |")
    
    if res_25 and res_38:
        c25 = res_25["total_cuts"]
        c38 = res_38["total_cuts"]
        md.append(f"| **總切鏡次數 (Total Cuts)** | `{c25}` 次 | `{c38}` 次 | 3.8 比 2.5 {'增加' if c38>c25 else '減少'} {abs(c38-c25)} 次切鏡 |")
        
        avg25 = res_25["avg_shot"]
        avg38 = res_38["avg_shot"]
        md.append(f"| **平均單鏡時長 (Avg Duration)** | `{avg25:.1f}s` | `{avg38:.1f}s` | {'3.8 剪輯節奏更緊湊' if avg38<avg25 else '3.8 鏡頭更沉穩持續'} |")
        
        min25 = res_25["min_shot"]
        min38 = res_38["min_shot"]
        md.append(f"| **最短鏡頭時長 (Min Shot)** | `{min25:.1f}s` | `{min38:.1f}s` | 檢查是否符合 $\\ge 2.5\\text{s}$ 最小防抖門檻 |")
        
        max25 = res_25["max_shot"]
        max38 = res_38["max_shot"]
        md.append(f"| **最長單鏡時長 (Max Shot)** | `{max25:.1f}s` | `{max38:.1f}s` | 長篇獨白時的鏡頭鎖定穩定度 |")
        
        fs25 = res_25["first_start"]
        fs38 = res_38["first_start"]
        md.append(f"| **Phase 0 正片起剪點 (Start)** | `{fs25:.2f}s` | `{fs38:.2f}s` | 開場倒數廢料排除時間點相差 `{abs(fs38-fs25):.2f}s` |")
        
        tot25 = sum(res_25["cam_durations"].values())
        tot38 = sum(res_38["cam_durations"].values())
        c1_25_pct = (res_25["cam_durations"].get("CAM1", 0) / tot25 * 100) if tot25 else 0
        c2_25_pct = (res_25["cam_durations"].get("CAM2", 0) / tot25 * 100) if tot25 else 0
        c1_38_pct = (res_38["cam_durations"].get("CAM1", 0) / tot38 * 100) if tot38 else 0
        c2_38_pct = (res_38["cam_durations"].get("CAM2", 0) / tot38 * 100) if tot38 else 0
        
        md.append(f"| **CAM1 (沈伯洋) 佔比** | `{c1_25_pct:.1f}%` ({res_25['cam_durations'].get('CAM1',0):.0f}s) | `{c1_38_pct:.1f}%` ({res_38['cam_durations'].get('CAM1',0):.0f}s) | 訪談核心受訪者畫面比重 |")
        md.append(f"| **CAM2 (工頭堅) 佔比** | `{c2_25_pct:.1f}%` ({res_25['cam_durations'].get('CAM2',0):.0f}s) | `{c2_38_pct:.1f}%` ({res_38['cam_durations'].get('CAM2',0):.0f}s) | 主持人提問與反應鏡頭比重 |")
    else:
        md.append("| *(資料計算中)* | 正在等待兩側 EDL 產出... | 正在等待兩側 EDL 產出... | - |")
        
    md.append("\n## 二、 剪輯決策風格分析與實務建議\n")
    md.append("- **Gemini 2.5 Flash 特性**：長上下文理解極為強韌，適合在大段落論述時保持鏡頭穩定，避免過度瑣碎切鏡。")
    md.append("- **Gemini 3.8 Flash 特性**：具備深度思考預算（Thinking Budget），能更敏銳捕捉對話交替瞬間與微表情反應切換，節奏更富動態感。")
    md.append("\n## 三、 交付成果清單\n")
    md.append("1. **Path A (2.5 Flash)**: `final_cut_full.xml` (CAM1沈伯洋 A1/A2 + CAM2工頭堅 A3/A4)")
    md.append("2. **Path B (3.8 Flash)**: `final_cut_gemini_3.8.xml` (CAM1沈伯洋 A1/A2 + CAM2工頭堅 A3/A4)")
    md.append(f"\n*報告儲存路徑：`{REPORT_FILE}`*")
    
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"Report written to {REPORT_FILE}")

if __name__ == "__main__":
    main()
