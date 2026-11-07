# Third-party notices

video2obsidian does not bundle third-party source code. It installs the following packages from PyPI through `requirements.txt`. Licences were read from each package's installed metadata on 2026-11-07.

| Package | Version checked | Licence |
|---|---|---|
| fastapi | 0.162.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| requests | 2.34.2 | Apache-2.0 |
| faster-whisper | 1.2.1 | MIT |
| yt-dlp | 2026.10.26 | Unlicense |
| imageio-ffmpeg | 0.7.0 | BSD-2-Clause (the FFmpeg binary it downloads has its own licence) |
| playwright | 1.64.1 | Apache-2.0 |
| opencc-python-reimplemented | 0.1.7 | Apache-2.0 |
| rapidocr_onnxruntime | 1.4.4 | Apache-2.0 |
| Pillow | 13.0.0 | MIT-CMU |

Key packages installed automatically as dependencies of the above (licences from installed metadata, 2026-11-08):

| Package | Version checked | Licence | Pulled in by |
|---|---|---|---|
| ctranslate2 | 4.8.5 | MIT | faster-whisper |
| huggingface-hub | 2.4.0 | Apache-2.0 | faster-whisper (model download) |
| av | 19.0.2 | BSD-3-Clause | faster-whisper |
| tokenizers | 0.23.3 | Apache-2.0 | faster-whisper |
| onnxruntime | 1.31.1 | MIT | rapidocr_onnxruntime |
| opencv-python | 5.0.0.93 | Apache-2.0 | rapidocr_onnxruntime |
| numpy | 2.5.4 | BSD-3-Clause and others | several |

Models downloaded at first run: Whisper models by OpenAI (MIT), in the CTranslate2 format published by SYSTRAN on Hugging Face.


Optional external tools (not included, installed separately by the user): Obsidian, Tailscale, Google Drive for desktop, Ollama (MIT) or another OpenAI-compatible LLM endpoint, and AgentChat (listed below). LLM models such as Qwen have their own licences.

## Optional external tool (not bundled)

- **AgentChat** by ziwang-Physics, MIT licence: https://github.com/ziwang-Physics/AgentChat
  Not included in this repository. video2obsidian only calls it as a separate program when the user configures it.
