# video2obsidian

Turn a video link into a searchable, timestamped note in an Obsidian vault.

Paste a link (or share it from a phone), and the tool downloads the video, transcribes it, writes an AI summary and saves a Markdown note with key frames and clickable timestamps.

> Personal learning project by **Chong Jia You** ([@programmermars](https://github.com/programmermars)). Built with AI coding assistants; the design, integration, testing and debugging are my own work.

## What it does

- **Download**: Douyin (headless browser), and other sites through yt-dlp; picks a low-bitrate stream with audio by default.
- **Transcribe**: faster-whisper on a GPU worker, with automatic CPU fallback and resume after interruption.
- **Summarise**: sends the transcript to an LLM backend (any OpenAI-compatible endpoint); checks that the summary is complete and tries the next backend if not.
- **Write notes**: Markdown note with front matter, key-frame screenshots and timestamp links; de-duplicates by video ID.
- **Run as a service**: FastAPI web UI and REST API, persisted job queue that survives restarts, automatic retry, health-check endpoint and run logs.

## Quick start (Windows)

1. Install Python 3.11 or 3.12 (3.13 is not supported yet: `rapidocr_onnxruntime` has no 3.13 build).
2. Run `install.bat` (creates `.venv`, installs `requirements.txt`, creates `config.json` from `config.example.json` and downloads the Whisper model).
3. Edit `config.json` and set at least `vault_path` and `llm_base_url`.
4. Run `start.bat`, then open http://127.0.0.1:8791

## API (main endpoints)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/jobs` | submit one or more video links |
| GET | `/api/jobs` | job list and progress |
| GET | `/api/search?q=` | search existing notes |
| GET | `/api/health` | health check |

## How it was tested

Verified with real runs (Douyin and Instagram videos, GPU and CPU transcription, summary fallback, restart recovery, automatic retry) and with offline tests of the parsing and formatting helpers. Known limitations: English transcription on the CPU fallback is noticeably weaker than on the GPU worker, and outline timestamps can drift by 20–40 seconds.

## Responsible use

- This tool is for **personal study notes**. Only process videos you are allowed to download, and follow each platform's terms of service and local copyright law.
- Do not redistribute downloaded videos, transcripts or summaries of other people's content.
- This repository contains **no** downloaded media, transcripts, notes, cookies or API keys. Keep your own `config.json` private.

## Licence

MIT, see [LICENSE](LICENSE). Third-party packages keep their own licences, see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
