# video2obsidian

Turn a video link into a searchable, timestamped note in an Obsidian vault.

Paste a link (or share it from a phone), and the tool downloads the video, transcribes it, writes an AI summary and saves a Markdown note with key frames and clickable timestamps that jump to the matching part of the transcript.

> Personal learning project by **Chong Jia You** ([@programmermars](https://github.com/programmermars)). Built with AI coding assistants; the design, integration, testing and debugging are my own work.

## What it does

- **Download**: Douyin (headless browser), and other sites through yt-dlp; picks a low-bitrate stream with audio by default.
- **Transcribe**: faster-whisper on a GPU worker, with automatic CPU fallback and resume after interruption.
- **Summarise**: sends the transcript to an LLM backend (any OpenAI-compatible endpoint); checks that the summary is complete and tries the next backend if not.
- **Write notes**: Markdown note with front matter, key-frame screenshots and timestamp links; de-duplicates by video ID.
- **Run as a service**: FastAPI web UI and REST API, persisted job queue that survives restarts, automatic retry, health-check endpoint and run logs.

## Requirements

- Python **3.11 or 3.12** (3.13 is not supported yet: `rapidocr_onnxruntime` has no 3.13 build).
- A folder to use as the notes vault. **Obsidian is optional**: notes are plain Markdown files, but Obsidian is recommended to see key frames and use the timestamp links.
- Optional: an LLM for summaries (local [Ollama](https://ollama.com) or any OpenAI-compatible API), an NVIDIA GPU for faster transcription (otherwise CPU is used automatically).

## Quick start

### Windows (one command, in cmd)

If you don't have Python 3.11/3.12 yet: `winget install -e --id Python.Python.3.12`, then open a new cmd window.

```
cd /d %USERPROFILE% && curl -L -o v2o.zip https://github.com/programmermars/video2obsidian/archive/refs/heads/main.zip && tar -xf v2o.zip && cd video2obsidian-main && install.bat && notepad config.json
```

Wait for `INSTALL_OK`, set `vault_path` (and `llm_base_url` / `llm_model` for summaries) in `config.json`, save, then run `start.bat`. The web UI opens at http://127.0.0.1:8791

### macOS / Linux (one command, in a terminal)

```
git clone https://github.com/programmermars/video2obsidian && cd video2obsidian && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt && cp config.example.json config.json && ${EDITOR:-nano} config.json && .venv/bin/python app.py
```

If Chrome or Edge is not installed and you want Douyin downloads, also run `.venv/bin/playwright install chromium`. The `.bat`/`.vbs` helpers are Windows-only.

**Check it works**: open http://127.0.0.1:8791/api/health and look for `"ok": true`.

Both commands were tested from a clean install (Windows 11 with Python 3.11, Linux with Python 3.12).

### Install with an AI assistant

Paste this into an AI coding assistant that can run commands on your computer (for example Claude Code or Cursor):

```
Please install and run the open-source project video2obsidian on this computer:
https://github.com/programmermars/video2obsidian

1. Read README.md first and follow it.
2. Check the Python version: it must be 3.11 or 3.12 (3.13 is not supported). If missing, ask me before installing 3.12.
3. On Windows run install.bat; on macOS/Linux create a venv and pip install -r requirements.txt
   (and run "playwright install chromium" if Chrome/Edge is not installed).
4. Create config.json from config.example.json, then ask me for:
   - the notes vault folder (vault_path); create it if it does not exist;
   - whether I want AI summaries: local Ollama (llm_base_url = http://localhost:11434/v1)
     or an OpenAI-compatible API (I will provide the API key).
5. Start the app and open http://127.0.0.1:8791/api/health; confirm "ok": true.
6. Run one full job with a public short video link or a local video file I give you,
   and confirm a note was written to the vault.

Safety rules:
- Do not change "host" in config.json to 0.0.0.0 and do not set up port forwarding: the app has no login.
- Put API keys only in config.json; never in other files or git commits.
- Ask me before installing software or deleting files.
- If something fails, show me the error from the logs folder and explain it.

When done, tell me how to start it, how to submit a link and where the notes are saved.
```

## Security

The app has **no login or password**. It listens on `127.0.0.1` by default, so only your own computer can reach it. To use it from your phone, use a private network such as Tailscale (`phone-access.bat`). Never expose it to the public internet.

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
