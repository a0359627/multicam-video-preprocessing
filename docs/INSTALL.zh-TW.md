# 多機位粗剪：同事安裝與使用指南

此指南使用 [盛威維護的 fork](https://github.com/a0359627/multicam-video-preprocessing)，分支為 `feat/gemini-3.8-test-c-workflow`。不要改裝原作者 repo 或預設 `main`。本套件源自 [sylphlin](https://github.com/sylphlin/multicam-video-preprocessing)，保留原作者資訊與 MIT 授權。

只要有各機位的影片與可互相比對的現場錄音，就能同步、產生 XML，並輸出 MP4。**OBS／外錄母帶是選項，沒有也能成片。** 若各機位完全沒有共同錄到的聲音，程式無法靠聲學比對同步，需先確認素材來源。

## 1. 新電腦安裝

需要 Python 3.10 以上（建議 3.11 或 3.12）、Git、FFmpeg／ffprobe，以及 Google Cloud CLI。以下指令以 macOS 終端機為準；Windows 安裝、符號連結與剪輯軟體匯入尚未在本次驗收中確認。

若已安裝 Homebrew，可先安裝 FFmpeg：

```bash
brew install ffmpeg
```

下載正確分支，建立本專案獨立的 Python 環境：

```bash
git clone --branch feat/gemini-3.8-test-c-workflow --single-branch https://github.com/a0359627/multicam-video-preprocessing.git
cd multicam-video-preprocessing
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python --version
ffmpeg -version
ffprobe -version
gcloud --version
```

若由 Agent 操作，讓它開啟這個 repo，先讀 `AGENTS.md` 與 `skills/multicam-video-preprocessing/SKILL.md`，使用 `.venv` 內的 Python 執行官方腳本。不要把測試個案 `scripts/benchmarks/interview_test_c/` 當正式入口；裡面的舊素材路徑和偏移量只用於歷史比較。

## 2. 用自己的 Google 帳號授權

製作部已獲授權的同事沿用既有雲端環境，不必建立 project 或 bucket。請在登入頁選擇**已被加入授權的本人帳號**：

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project panmedia-internal-ge
```

如果同一台電腦之前的 single-cam 已用這個帳號完成 ADC 授權，而且仍有效，可以沿用；確認配額專案為 `panmedia-internal-ge`。一般 `gcloud auth login` 與程式需要的 ADC 不同，僅登入 CLI 不一定足夠。

每次開新終端機，先進 repo、啟用虛擬環境，再帶入以下設定：

```bash
source .venv/bin/activate
export GOOGLE_CLOUD_PROJECT=panmedia-internal-ge
export GOOGLE_CLOUD_LOCATION=global
export GCS_BUCKET=panmedia-test-488409-agent-staging
```

這三個值是環境識別資訊，沒有 API key。也可以自行建立本機 `.env` 保存這三行設定；`.env` 已被 Git 忽略。不要複製別人的 ADC、token、服務帳戶金鑰或 `.env`。

**不要執行 `setup.sh` 當作新電腦授權步驟。** 它會建立或修改 bucket、IAM、生命週期等雲端配置，只供管理員另建環境時使用。若遇 401／403，保留錯誤原文，先核對本人帳號、ADC 和上述設定，再請管理員確認授權；不要自行改雲端權限。

## 3. 沒有 OBS：直接用機位聲音

每組素材指定一個新的輸出目錄，下例為 `output/episode-001`。有空白的檔案路徑務必加引號。

Stage 1：同步機位、標準化音量、產生已同步素材與多畫面分析片。

```bash
python skills/multicam-video-preprocessing/scripts/multicam_pipeline.py \
  --ref "/path/to/CAM1.mp4" \
  --targets "/path/to/CAM2.mp4" \
  --normalize --merge --strict-sync \
  -o output/episode-001
```

確認 `multicam_sync.json`、各機位同步影片與 `multicam_merged_full.mp4` 都存在且非空。同步影片沿用來源檔名（例如 `CAM1_synced.mp4` 或 `A機_synced.MP4`）；實際路徑以 `multicam_sync.json` 的 `synced_path` 為準。`--strict-sync` 會在機位對齊可信度不足時停止；沒有 OBS 不需要補任何替代參數。

Stage 2：用全長分析片產生切鏡表。這一步會上傳影片並呼叫已授權的 Vertex AI，使用共用雲端配額。

```bash
python skills/multicam-video-preprocessing/scripts/generate_edl.py \
  --video output/episode-001/multicam_merged_full.mp4 \
  --strict-edl --lang zh-TW
```

預設採通用 Test C 自然節奏規則 `assets/prompt_c_portable.md`，保留主持人開場、語意換鏡和有理由的反應鏡頭，不套用舊個案人名或逐字稿。完成後確認 `edl_full.csv`、`edl_full_report.md` 存在，且驗證沒有 ERROR。若要加入本次訪談人物與大綱，可用 `--template /path/to/本次提示詞.md` 明確指定，不修改套件內歷史測試資料。

Stage 3A：產生可繼續精剪的 XML。

```bash
python skills/multicam-video-preprocessing/scripts/export_fcp7_xml.py \
  --dir output/episode-001 \
  --output output/episode-001/final_cut_full.xml \
  --strict-edl --lang zh-TW
```

Stage 3B：產生可播放的 MP4。

```bash
python skills/multicam-video-preprocessing/scripts/edl_to_video.py \
  --edl output/episode-001/edl_full.csv \
  --media-dir output/episode-001 \
  --output output/episode-001/final_cut_full.mp4 \
  --strict-edl --lang zh-TW
```

沒有母帶時，分析片與 MP4 連續混合各機位的已同步音訊，每路以相同 `1/N` 比例混合，不隨切鏡突然換收音。兩台機位皆為立體聲時，XML 保留四軌機位音訊與相應音量，剪輯師可以在時間軸中調整、關閉個別機位。成品音質取決於相機實際錄下的聲音；素材若有串音、環境音，可在 XML 中進一步處理。

## 4. 有 OBS／外錄母帶：只在 Stage 1 多給一個檔案

使用相同三階段流程，Stage 1 增加 `--master-audio` 即可：

```bash
python skills/multicam-video-preprocessing/scripts/multicam_pipeline.py \
  --ref "/path/to/CAM1.mp4" \
  --targets "/path/to/CAM2.mp4" \
  --master-audio "/path/to/OBS.mkv" \
  --normalize --merge --strict-sync \
  -o output/episode-002
```

也可指定 WAV 錄音檔。程式以聲音量測母帶相對於參考相機的偏移，記錄於本次 `multicam_sync.json`，後續 XML／MP4 自動使用對齊結果。不要手動套入舊案的 `18.268` 秒；素材路徑、影格率與解析度由本次輸入決定。

母帶對齊可信度不足或不足以涵蓋所需時間時會停止，不會默認偏移為零或靜默換回機位聲音。排除問題後重新執行 Stage 1；若決定不用該母帶，使用新的輸出目錄，明確省略 `--master-audio` 重新執行。

兩台機位與母帶皆為立體聲時，XML 有六條音軌：A1／A2 母帶啟用，A3～A6 各機位音訊保留但預設關閉。若素材為單聲道，依實際聲道數產生音軌。MP4 使用對齊母帶。

## 5. 同名上傳與清理

本機分析片可一直叫 `multicam_merged_full.mp4`，程式自動上傳到：

```text
gs://panmedia-test-488409-agent-staging/raw/<本次工作唯一編號>/multicam_merged_full.mp4
```

不同人、不同工作不會因同名互相覆寫。同次程式執行內的重試沿用同一物件；重新執行新的工作會用新路徑上傳，不跨工作共用快取。若 Stage 2 加 `--cleanup-gcs`，只清理本次建立的暫存物件；直接提供的 `gs://` 輸入不會被自動刪除。

雲端保留政策維持 `raw/` 2 天、`output/`／`deliverables/`／`multicam_assets/` 15 天。這是雲端暫存清理政策，不代表本機 XML、媒體或 MP4 已備份到雲端。

## 6. 完成前確認

- 每個步驟成功退出，所需產物存在且非空。失敗時保留錯誤原文並停止，不跳到下一階段。
- 播放 `final_cut_full.mp4`，確認首、中、尾有聲音且影音同步，切鏡時聲音連續。
- 將 XML 匯入 DaVinci Resolve，確認影片與音軌都有連到素材，沒有 Media Offline，且能修改切點和音量。
- 檢查主持人開場、人物切換、反應鏡頭及尾段。模型完成與 XML 匯入成功不代表剪輯品質已獲接受。
- 交付可編輯工程時，連同 XML 引用的同步媒體及母帶一起交付。搬移後必要時在剪輯軟體重新連結媒體，不能只交 XML。

## 7. 之後更新

先結束正在跑的工作，再於本 repo 更新正確分支：

```bash
git switch feat/gemini-3.8-test-c-workflow
git pull --ff-only origin feat/gemini-3.8-test-c-workflow
source .venv/bin/activate
python -m pip install -r requirements.txt
```

若本機有自行修改，先保存變更，遇衝突時停止，不強制覆寫。更新只從上述 fork 接收；原作者更新由維護者評估後再納入。
