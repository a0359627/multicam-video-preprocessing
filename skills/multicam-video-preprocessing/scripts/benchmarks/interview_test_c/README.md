# 沈伯洋 × 工頭堅 50 分鐘雙機對談：Gemini 3.8 Flash 測試 C 基準評測

本目錄記錄 2026-09-30 在實際商業規格雙機 4K 訪談（沈伯洋 × 工頭堅《在台北住了四十年之後，我們算不算台北人？》）中，測試與演進多機 AI 剪輯管線的完整實驗記錄與評測代碼。

## 歷史資料範圍與目前入口（2026-10-08）

本目錄是 2026-09-30 個案的歷史基準。下方人物、4K／29.97 fps、音軌與 `18.268` 秒偏移都只描述該次實驗，不能套用於其他素材，也不表示新版已完成 DaVinci 或同事電腦驗收。

新素材與新電腦請依 [同事安裝與使用指南](../../../../../docs/INSTALL.zh-TW.md)，使用正式三階段腳本。可在 Stage 1 指定 `--master-audio` 自動量測母帶偏移；沒有 OBS 時沿用已同步機位音訊，仍可輸出 XML 與 MP4。

- `export_c_with_master_audio.py` 自本次修正起是相容入口，委派給 [正式 XML 匯出器](../../export_fcp7_xml.py)。需傳入 `--dir OUTPUT_DIR --strict-edl --lang zh-TW`，讀取該目錄的 EDL／同步資料；不再使用個案固定路徑或偏移。原版本保留於 Git commit `39bc03b`。
- `run_production_cut.py` 雖名為 production，仍是歷史個案執行器，內含維護者電腦、外接碟、素材及提示詞的固定路徑。**不要用它安裝、處理新素材或作為通用排程入口。** `run_path_b_gemini_38.py`、V2 匯出器及下載輔助程式同樣保留作歷史比較。
- 通用 Test C 提示詞為 `assets/prompt_c_portable.md`。舊 `assets/prompt_c_natural_rhythm.md` 包含本集人物與逐字稿，僅作個案參考。

---

## 1. 測試資料集規格

- **影片長度**：原始 50 分 50 秒，有效剪輯片長 49 分 25.5 秒。
- **機位配置**：
  - **CAM1（受訪來賓 / 沈伯洋）**：4K 3840×2160 @ 29.97 fps (NTSC)，桌上型麥克風獨立收音。
  - **CAM2（節目主持 / 工頭堅）**：4K 3840×2160 @ 29.97 fps (NTSC)，獨立麥克風收音。
  - **現場 Master 混音軌**：`OBS.mkv` 現場雙人混音 WAV 聲軌（與 CAM 物理時間存在 +18.268s 偏移）。
- **硬體與雲端環境**：
  - Apple Silicon Mac（VideoToolbox 硬體加速編碼）
  - Google Cloud Vertex AI ADC (`gemini-3.8-flash`, `location: global`)
  - GCS 自動銷毀暫存 Bucket (`panmedia-test-488409-agent-staging`)

---

## 2. 演進歷程與 A / B / C 測試比較

| 版本 | 核心機制 | 總切鏡數 | 風格與評價 |
| :--- | :--- | :--- | :--- |
| **Path A** | Gemini 2.5 Flash | 56 鏡 | 偏防禦性收斂，鏡頭沉穩但節奏偏悶，38 秒處出現同機位假性自切。 |
| **Path B** | Gemini 3.8 Flash 初版 | 78 鏡 | 影像理解靈敏、節奏活躍，但開場主持人尚未介紹完來賓即提早切給來賓。 |
| **V2** | 外部 Python 規則硬切 | 85 鏡 | 演算法定時強制切割長鏡頭，缺乏語意理解，顯得機械化頓挫。 |
| **測試 C (Test C)** | Gemini 3.8 Flash 原生語意＋自然節奏 | **100 鏡** | **最優平衡**：開場主持人完整鎖定 71.7 秒後平滑交接，精準捕捉 4 處有機反應鏡頭，0 假性自切。 |

---

## 3. 測試 C 核心亮點

1. **開場主持權剛性收斂**：
   - 第一顆鏡頭自 `00:24.500` 持續至 `01:36.200`（整整 71.7 秒），全程 100% 鎖定主持人工頭堅（CAM2），直至沈伯洋開口問好才切至 CAM1。
2. **有機化學反應鏡頭（Organic Reactions）**：
   - `02:11.500`（3.0s）：工頭堅展示超人力霸王，捕捉沈伯洋驚呼微笑。
   - `02:30.000`（3.2s）：工頭堅展示無敵鐵金剛，捕捉沈伯洋點頭讚嘆。
   - `08:02.000`（3.5s）：沈伯洋談文夏禁歌，捕捉工頭堅點頭微笑附和。
   - `12:48.500`（7.5s）：沈伯洋以南北粽幽默比喻，捕捉工頭堅大笑調侃東泉辣椒醬。
3. **6 軌廣播級音訊架構**：
   - A1 / A2：OBS 現場雙人高品質混音（精準補償 +18.268s 偏移）。
   - A3 / A4：CAM1 沈伯洋獨立收音軌。
   - A5 / A6：CAM2 工頭堅獨立收音軌。
4. **DaVinci Resolve 標記**：
   - 橘色 Marker 標記主講發話片段，藍色 Marker 標記聆聽反應片段。

---

## 4. 本目錄腳本清單

- `run_path_b_gemini_38.py`：Gemini 3.8 Flash 平行管線執行腳本（含 2 Mbps Compact Grid 壓縮）。
- `export_c_with_master_audio.py`：目前為正式 XML 匯出器的相容入口；原測試 C 專屬實作見 `39bc03b`。
- `export_v2_with_master_audio.py`：V2 演算法硬切實驗對照腳本。
- `compare_cuts.py`：Path A 與 Path B / C 之切鏡時長與統計指標對比工具。
- `run_production_cut.py`：含固定個案路徑的歷史端到端排程與日誌記錄器，不供新素材或新電腦使用。
- `edl_gemini_3.8_c_clean.csv`：測試 C 經清洗後的 100 鏡精準 EDL 決策表。
- `edl_gemini_3.8_c_report.md`：測試 C 之 Vertex AI 推論指標與語意驗證報告。
- `download_footage.py` / `download_b_cam.py` / `monitor_downloads.py`：大檔母帶下載與監控輔助工具。
