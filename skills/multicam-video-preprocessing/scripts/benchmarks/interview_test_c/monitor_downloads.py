#!/usr/bin/env python3
"""
Chrome Download Watcher and Auto-Mover for Crucial X10 External SSD
Watches ~/Downloads for in-progress .crdownload files, logs live progress,
and automatically moves completed MP4 masters into /Volumes/Crucial X10/muticam_test_file
"""

import os
import sys
import time
import shutil
import glob
from pathlib import Path

DOWNLOADS_DIR = Path.home() / "Downloads"
TARGET_DIR = Path("/Volumes/Crucial X10/muticam_test_file")

TARGET_FILES = ["A機.MP4", "B機.MP4"]


def format_bytes(b: int) -> str:
    return f"{b / (1024**3):.2f} GB"


def main():
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 65)
    print(f"啟動 Chrome 下載監控守護服務")
    print(f"來源目錄: {DOWNLOADS_DIR}")
    print(f"目標目錄: {TARGET_DIR}")
    print("=" * 65)

    # Copy outline if present in Downloads
    outline_src = DOWNLOADS_DIR / "訪談大綱_沈伯洋_台北東京.docx"
    if outline_src.exists():
        outline_dest = TARGET_DIR / "訪談大綱_沈伯洋_台北東京.docx"
        if not outline_dest.exists() or outline_dest.stat().st_size != outline_src.stat().st_size:
            shutil.copy2(outline_src, outline_dest)
            print(f"[✓] 訪談大綱已同步至外接硬碟: {outline_dest.name}")

    start_time = time.time()
    last_log_time = 0
    moved_files = set()

    # Check if target already has them
    for tf in TARGET_FILES:
        target_path = TARGET_DIR / tf
        if target_path.exists() and target_path.stat().st_size > 10 * (1024**3):
            print(f"[INFO] 目標外接硬碟已有完整檔案: {tf} ({format_bytes(target_path.stat().st_size)})")
            moved_files.add(tf)

    while len(moved_files) < len(TARGET_FILES):
        now = time.time()
        cr_files = list(DOWNLOADS_DIR.glob("*.crdownload"))

        # Look for completed target files in Downloads that are no longer downloading
        for f in DOWNLOADS_DIR.iterdir():
            if f.name in TARGET_FILES and f.name not in moved_files:
                # Ensure no corresponding crdownload or active write
                size1 = f.stat().st_size
                time.sleep(1)
                size2 = f.stat().st_size
                if size1 == size2 and size1 > 10 * (1024**3):
                    dest = TARGET_DIR / f.name
                    print(f"\n[MOVE] 檢測到 {f.name} 下載完成 ({format_bytes(size1)})，正在移動至外接硬碟...")
                    shutil.move(str(f), str(dest))
                    print(f"[✓ SUCCESS] 已安全移至: {dest}")
                    moved_files.add(f.name)

        if len(moved_files) >= len(TARGET_FILES):
            break

        # Log status every 15 seconds
        if now - last_log_time >= 15.0:
            if cr_files:
                details = []
                for cr in cr_files:
                    try:
                        sz = cr.stat().st_size
                        details.append(f"{cr.name[:18]}.. ({format_bytes(sz)})")
                    except Exception:
                        pass
                print(f"[{time.strftime('%H:%M:%S')}] 正在下載中 ({len(cr_files)} 個檔案): {', '.join(details)}")
            else:
                print(f"[{time.strftime('%H:%M:%S')}] 等待檔案完成寫入... (已完成: {list(moved_files)})")
            sys.stdout.flush()
            last_log_time = now

        time.sleep(3)

    print("\n" + "=" * 65)
    print("雙機位母帶已全數下載並安全移動至外接硬碟！")
    for tf in TARGET_FILES:
        fp = TARGET_DIR / tf
        if fp.exists():
            print(f"  ✓ {tf}: {format_bytes(fp.stat().st_size)}")
    print("=" * 65)


if __name__ == "__main__":
    main()
