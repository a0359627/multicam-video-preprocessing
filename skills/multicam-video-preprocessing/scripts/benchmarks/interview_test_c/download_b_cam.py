#!/usr/bin/env python3
"""
Dedicated Downloader for B-Cam Footage to Crucial X10 External SSD
"""

import os
import sys
import time
import requests

TARGET_DIR = "/Volumes/Crucial X10/muticam_test_file"
FILE_ID = "1_czTo4HzUIT5wbeysiZu9U-ihEzRLYjS"
FILENAME = "B機.MP4"
CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB


def get_remote_file_size(file_id: str) -> int:
    url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
    headers = {"Range": "bytes=0-1"}
    r = requests.get(url, headers=headers, stream=True, timeout=15)
    cr = r.headers.get("Content-Range")
    if cr and "/" in cr:
        return int(cr.split("/")[-1])
    cl = r.headers.get("Content-Length")
    if cl:
        return int(cl)
    return 0


def main():
    final_path = os.path.join(TARGET_DIR, FILENAME)
    part_path = final_path + ".part"

    total_size = get_remote_file_size(FILE_ID)
    total_gb = total_size / (1024**3)
    print(f"[{FILENAME}] 雲端遠端大小: {total_gb:.2f} GB ({total_size} bytes)")

    if os.path.exists(final_path) and os.path.getsize(final_path) == total_size:
        print(f"[SKIP] {FILENAME} 已下載完畢！")
        return

    downloaded = 0
    if os.path.exists(part_path):
        downloaded = os.path.getsize(part_path)
        print(f"[RESUME] 從既有檔案 {downloaded / (1024**3):.2f} GB 開始續傳...")

    url = f"https://drive.usercontent.google.com/download?id={FILE_ID}&export=download&confirm=t"
    max_retries = 30
    retry = 0

    while downloaded < total_size:
        headers = {"Range": f"bytes={downloaded}-"}
        try:
            print(f"[{FILENAME}] 連線中... 已完成 {downloaded / (1024**3):.2f} GB")
            with requests.get(url, headers=headers, stream=True, timeout=60) as resp:
                if resp.status_code not in (200, 206):
                    print(f"[WARN] HTTP {resp.status_code}, 10 秒後重試...")
                    time.sleep(10)
                    retry += 1
                    if retry > max_retries:
                        break
                    continue

                last_log = time.time()
                bytes_log = 0

                with open(part_path, "ab" if downloaded > 0 else "wb") as f:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        f.write(chunk)
                        chunk_len = len(chunk)
                        downloaded += chunk_len
                        bytes_log += chunk_len

                        now = time.time()
                        if now - last_log >= 8.0:
                            elapsed = now - last_log
                            speed = (bytes_log / (1024 * 1024)) / elapsed
                            pct = (downloaded / total_size) * 100
                            rem_gb = (total_size - downloaded) / (1024**3)
                            eta_min = (rem_gb * 1024) / speed / 60 if speed > 0 else 0
                            print(
                                f"  > [{FILENAME}] {pct:5.1f}% | "
                                f"{downloaded / (1024**3):.2f} / {total_gb:.2f} GB | "
                                f"速度: {speed:5.1f} MB/s | "
                                f"剩餘: {rem_gb:.2f} GB (約 {eta_min:.1f} 分鐘)"
                            )
                            sys.stdout.flush()
                            last_log = now
                            bytes_log = 0

        except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
            print(f"[WARN] 斷線 ({e})，5 秒後自動續傳...")
            time.sleep(5)
            retry += 1
            if retry > max_retries:
                raise

    if downloaded >= total_size and total_size > 0:
        os.rename(part_path, final_path)
        print(f"\n[✓] {FILENAME} 成功下載完畢！總大小: {os.path.getsize(final_path) / (1024**3):.2f} GB")


if __name__ == "__main__":
    main()
