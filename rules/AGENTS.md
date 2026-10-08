# Multi-Camera Video Preprocessing - Operational Invariants for AI Clients

When you execute tasks or skills from this plugin, you MUST follow these operational rules:

## 1. Strict Read-Only Execution & Direct CLI Invocation (Do Not Modify Plugin Code)
- All Python scripts (`skills/multicam-video-preprocessing/scripts/*.py`, symlinked at `scripts/*.py`), prompt specifications (`skills/multicam-video-preprocessing/assets/*.md`, symlinked at `assets/*.md`), and configuration files are read-only tools.
- Do NOT edit, patch, or rewrite any files in this plugin with `replace_file_content`, `write_to_file`, or shell commands.
- Do NOT write ad-hoc temporary Python scripts, custom synchronization code, or one-off shell algorithms.
- Resolve `<PLUGIN_ROOT>` as two directory levels above `skills/multicam-video-preprocessing/SKILL.md` (`../../` from the skill directory).
- Set `Cwd` to `<PLUGIN_ROOT>` and run the official scripts (`python3 skills/multicam-video-preprocessing/scripts/...` or `python3 scripts/...`) directly with `run_command` using the specified arguments. Do NOT search for global CLI aliases with `find_by_name` or `list_dir`.

## 2. Fail-Fast on Errors (Do Not Debug or Rewrite Code)
- If a script fails (exit code is not 0) or an external error occurs (such as 401 Unauthorized, 403 Forbidden, Quota Exceeded, missing Application Default Credentials, or missing FFmpeg):
  - Stop immediately.
  - Show the exact error message and exit status to the user.
  - Give a clear, actionable solution to the user (for example, use `gcloud auth application-default login`, set the approved quota project, or install FFmpeg). For the pre-authorized team environment, follow `docs/INSTALL.zh-TW.md`; do not run `setup.sh`, which changes cloud resources and is reserved for explicitly authorized provisioning.
  - Do NOT try to modify the script, probe different code paths, or rewrite logic.

## 3. Strict Zero-Emoji Policy in Technical Reports & Dynamic Language Mirroring
- Do NOT use decorative emojis or icons in generated technical EDL tables or validation Markdown reports unless defined by the official report schema.
- Keep all generated documentation and validation reports in plain, professional technical text.
- Always respond to the user in their prompt language (Traditional Chinese `zh-TW` when prompted in Traditional Chinese, English when prompted in English, Japanese when prompted in Japanese, etc.) and pass the matching `--lang` flag to validation scripts.

## 4. Acoustic Ground Truth & Zero-Split Pipeline Integrity
- Execute the 3-Stage Gated Workflow sequentially (`multicam_pipeline.py` -> `generate_edl.py` -> `export_fcp7_xml.py` / `edl_to_video.py`) as specified in `skills/multicam-video-preprocessing/SKILL.md`.
- All time alignments and EDL cut points must respect MFCC subframe acoustic alignment (<0.125ms) and the Zero-Split Agentic Video pipeline (`multicam_merged_full.mp4`).
- Never split full-length footage into intermediate chapters or use legacy AI Studio API keys (`GEMINI_API_KEY`).
- Verify all required stage exit criteria files exist and are non-empty (`> 0 bytes`) before proceeding to the next stage or declaring task completion.

## 5. Portable Optional Audio & Safe Shared Storage
- Stage 1 accepts optional `--master-audio` (WAV/OBS audio). Measure alignment to the reference camera and use `multicam_sync.json`; never reuse a benchmark offset or personal path. Low-confidence master alignment or insufficient coverage is a stop gate.
- Without a master, XML and MP4 must still use synchronized camera audio. Mix camera audio continuously at equal `1/N` gains; preserve independently editable tracks in XML. Two stereo cameras produce four camera audio tracks, or six tracks with a stereo master (master enabled, camera tracks disabled but retained). Mono sources retain their actual channel count.
- Run Stage 1 with `--strict-sync`, and use a separate local output directory for each recording. Acoustic synchronization requires sound recorded in common by the sources.
- Use `assets/prompt_c_portable.md` for general Test C editing. The historical `prompt_c_natural_rhythm.md` and `benchmarks/interview_test_c/` contain case-specific context and are not installation entry points.
- GCS staging uses `raw/<job UUID>/<filename>`. Internal retries can reuse the current job's object; new jobs use a new upload. `--cleanup-gcs` may only remove objects owned by the current job, never directly supplied `gs://` inputs. Keep the existing 2-day raw / 15-day deliverable lifecycle.
- Credentials belong to each user. Never copy ADC, tokens or secret files between machines. Obtain the fork from `a0359627/multicam-video-preprocessing`, branch `feat/gemini-3.8-test-c-workflow`; preserve upstream author credit.
