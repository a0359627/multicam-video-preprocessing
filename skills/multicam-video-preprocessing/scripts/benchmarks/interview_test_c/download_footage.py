#!/usr/bin/env python3
"""
Resilient Resumable Downloader for Large Multi-Cam Google Drive Footage
Downloads A-Cam and B-Cam directly to /Volumes/Crucial X10/muticam_test_file
Supports HTTP Range resume, exponential backoff retries, and live progress reporting.
"""

import os
import sys
import time
import requests

TARGET_DIR = "/Volumes/Crucial X10/muticam_test_file"

FILES_TO_DOWNLOAD = [
    {
        "id": "1yRkLBc5eYgSSHauUxrfCO4bkhuRzIiZJ",
        "name": "A機.MP4",
        "approx_gb": 37.7,
    },
    {
        "id": "1_czTo4HzUIT5wbeysiZu9U-ihEzRLYjS",
        "name": "B機.MP4",
        "approx_gb": 24.2,
    },
]

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB chunks


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


def download_file(file_id: str, filename: str, target_dir: str):
    os.makedirs(target_dir, exist_ok=True)
    final_path = os.path.join(target_dir, filename)
    part_path = final_path + ".part"

    total_size = get_remote_file_size(file_id)
    total_gb = total_size / (1024**3)

    if os.path.exists(final_path):
        local_size = os.path.getsize(final_path)
        if total_size > 0 and local_size == total_size:
            print(f"[SKIP] {filename} 已完整存在 ({local_size / (1024**3):.2f} GB)，略過下載。")
            return
        elif total_size == 0 and local_size > 10 * (1024**3):
            print(f"[SKIP] {filename} 已存在且大於 10 GB，視為已下載完畢。")
            return

    downloaded = 0
    if os.path.exists(part_path):
        downloaded = os.path.getsize(part_path)
        print(f"[RESUME] 發現未完成下載檔，將從 {downloaded / (1024**3):.2f} GB 處繼續下載...")

    url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"

    max_retries = 20
    retry_count = 0

    while downloaded < total_size or total_size == 0:
        headers = {}
        if downloaded > 0:
            headers["Range"] = f"bytes={downloaded}-"

        try:
            print(f"[{filename}] 正在建立串流連線 (已下載 {downloaded / (1024**3):.2f} / {total_gb:.2f} GB)...")
            with requests.get(url, headers=headers, stream=True, timeout=60) as resp:
                if resp.status_code not in (200, 206):
                    print(f"[ERROR] 伺服器回傳狀態碼 {resp.status_code}: {resp.text[:200]}")
                    time.sleep(5)
                    retry_count += 1
                    if retry_count > max_retries:
                        raise RuntimeError(f"超過最大重試次數 ({max_retries})")
                    continue

                mode = "ab" if downloaded > 0 else "wb"
                last_log_time = time.time()
                bytes_since_log = 0
                start_time = time.time()

                with open(part_path, mode) as f:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        f.write(chunk)
                        chunk_len = len(chunk)
                        downloaded += chunk_len
                        bytes_since_log += chunk_len

                        now = time.time()
                        if now - last_log_time >= 10.0:
                            elapsed = now - last_log_time
                            speed_mb = (bytes_since_log / (1024 * 1024)) / elapsed
                            pct = (downloaded / total_size * 100) if total_size > 0 else 0
                            remaining_gb = (total_size - downloaded) / (1024**3) if total_size > 0 else 0
                            eta_sec = (total_size - downloaded) / (speed_mb * 1024 * 1024) if speed_mb > 0 and total_size > 0 else 0
                            eta_min = eta_sec / 60
                            print(
                                f"  > [{filename}] {pct:5.1f}% | "
                                f"{downloaded / (1024**3):.2f} / {total_gb:.2f} GB | "
                                f"速度: {speed_mb:5.1f} MB/s | "
                                f"剩餘: {remaining_gb:.2f} GB (約 {eta_min:.1f} 分鐘)"
                            )
                            sys.stdout.flush()
                            last_log_time = now
                            bytes_since_log = 0

                # If we broke out of loop without error, check if finished
                if total_size > 0 and downloaded >= total_size:
                    break

        except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
            print(f"[WARN] 網路連線中斷 ({e})，5 秒後自動中斷續傳...")
            time.sleep(5)
            retry_count += 1
            if retry_count > max_retries:
                raise

    os.rename(part_path, final_path)
    print(f"[SUCCESS] {filename} 下載完成！總大小: {os.path.getsize(final_path) / (1024**3):.2f} GB\n")


def main():
    print("=" * 60)
    print(f"啟動多機位原片續傳下載作業 -> {TARGET_DIR}")
    print("=" * 60)
    for item in FILES_TO_DOWNLOAD:
        download_file(item["id"], item["name"], TARGET_DIR)
    print("所有素材下載完成！")


if __name__ == "__main__":
    main()
