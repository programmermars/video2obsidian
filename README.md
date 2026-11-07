# video2obsidian

Turn a video link into a searchable, timestamped note in an Obsidian vault.

Paste a link (or share it from a phone), and the tool downloads the video, transcribes it, writes an AI summary and saves a Markdown note with key frames and clickable timestamps that jump to the matching part of the transcript.

> Created and maintained by **Chong Jia You** ([@programmermars](https://github.com/programmermars)). Built with the help of AI coding assistants; the design, integration, testing and debugging are my own work. See [Credits](#credits).

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

Both commands were tested from a clean install (Windows with Python 3.11, Linux with Python 3.12).

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

## Optional: summaries with AgentChat

Instead of an API or a local model, summaries can be written through **[AgentChat](https://github.com/ziwang-Physics/AgentChat)** by **[ziwang-Physics](https://github.com/ziwang-Physics)** (MIT licence). AgentChat drives the web chat pages of Gemini, ChatGPT or Claude in your own logged-in Chrome, so no API key is needed. It is a separate project: it is **not** included in this repository, and all credit for it goes to its author.

1. Install AgentChat by following its own README (Node.js, `npm install`, then log in to the AI chat sites once in Chrome).
2. Check it works: `node skills/AgentChat-OneWeb/index.js --smoke`
3. In `config.json`, set these three values and put `"agentchat"` first in `summary_backends`:

| Key | Example | Meaning |
|---|---|---|
| `agentchat_node` | `C:\\Program Files\\nodejs\\node.exe` | path to `node.exe` |
| `agentchat_entry` | `C:\\path\\to\\AgentChat\\skills\\AgentChat-OneWeb\\index.js` | path to AgentChat's OneWeb entry file |
| `agentchat_provider` | `gemini`, `chatgpt` or `claude` | which chat site to try first (AgentChat falls back to the others) |

```json
"summary_backends": ["agentchat", "openai"]
```

With this order, video2obsidian tries AgentChat first and falls back to your OpenAI-compatible backend (for example local Ollama) if AgentChat fails. Optional timeouts: `agentchat_timeout` and `agentchat_provider_timeout` (seconds).

> **Terms of service warning.** Automating the web chat interfaces of ChatGPT, Claude or Gemini may break those services' terms of use and can lead to rate limits or account restrictions. You are responsible for checking the terms of each service and for how you use your own accounts. If in doubt, use a local model (Ollama) or an official API instead.

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

## Credits

**Author**
- **Chong Jia You** ([@programmermars](https://github.com/programmermars)): idea, system design, integration of the download → transcription → summary → notes pipeline, testing on real videos, debugging and maintenance.

**Optional integration**
- **[AgentChat](https://github.com/ziwang-Physics/AgentChat)** by **[ziwang-Physics](https://github.com/ziwang-Physics)** (MIT): used for web-chat summaries when the user configures it. Not bundled; all credit for AgentChat goes to its author.

**Open-source projects this tool is built on**
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) by SYSTRAN (MIT): speech-to-text
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense): video downloads
- [FastAPI](https://github.com/fastapi/fastapi) (MIT) and [Uvicorn](https://github.com/encode/uvicorn) (BSD-3-Clause): web UI and API
- [Playwright](https://github.com/microsoft/playwright-python) by Microsoft (Apache-2.0): Douyin downloads in a headless browser
- [RapidOCR](https://github.com/RapidAI/RapidOCR) (Apache-2.0): on-screen subtitle OCR
- [OpenCC](https://github.com/yichen0831/opencc-python) (Apache-2.0): Traditional → Simplified Chinese
- [FFmpeg](https://ffmpeg.org) via imageio-ffmpeg: audio and key-frame extraction

Full package and licence list: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

**AI assistance**
- Parts of the code and documentation were written with AI coding assistants (including Claude). Some commits list Claude as co-author for transparency. All changes were reviewed, tested and integrated by the author.

## Licence

MIT, see [LICENSE](LICENSE). Third-party packages keep their own licences, see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
