# lecturn-kokoro-server

A small, self-contained FastAPI server that runs **Kokoro-82M** on your own
hardware and exposes the **OpenAI-compatible TTS API** that lecturn's
`--provider local` speaks. Point lecturn at it and narrate entire textbooks with
**no paid API and no per-request cost** — synthesis runs on your CPU/GPU.

It wraps the official [`kokoro`](https://pypi.org/project/kokoro/) PyTorch
package (with misaki G2P for correct English pronunciation — *not* kokoro-onnx,
whose default espeak G2P is a pronunciation regression for audiobooks).

> This app has its **own** `pyproject.toml` and virtualenv. The heavy ML stack
> (torch, kokoro, misaki) is intentionally kept out of lecturn's install — users
> who stick to hosted providers never download any of it.

## Requirements

- **Python 3.12** (kokoro 0.9.4 supports `>=3.10,<3.13`).
- **[uv](https://docs.astral.sh/uv/)**.
- **ffmpeg** on your `PATH` (used to encode MP3). Same prerequisite as lecturn.
- ~1–2 GB free disk for the torch wheel + Kokoro weights.

## Quickstart

```bash
cd server
uv sync                    # installs torch, kokoro, fastapi, … into server/.venv
uv run lecturn-kokoro      # serves on http://127.0.0.1:8880
```

The **first** synthesis (or `--warm`) downloads the Kokoro weights (~327 MB, plus
~0.5 MB per voice) from the Hugging Face Hub — this can take a few minutes on a
cold cache; it's logged and cached for next time. Everything after is local.

Options:

```bash
uv run lecturn-kokoro --host 127.0.0.1 --port 8880 --log-level info --warm
```

- `--warm` loads the model at startup so the first request isn't slow.
- Binds `127.0.0.1` by default. Binding a wider interface exposes an
  **unauthenticated** server — do so only if you understand the risk.

## Point lecturn at it

With the server running:

```bash
lecturn convert book.pdf --provider local
```

lecturn defaults `--provider local` to `http://127.0.0.1:8880/v1`, model
`kokoro`, voice `af_heart`. Override the URL with `--base-url` / `LECTURN`'s
local base-URL env var if you moved the server.

## Voices

`GET /v1/audio/voices` lists all **54** Kokoro voice IDs. Voice IDs are prefixed
`<lang><gender>` — the first letter is the language (`a` US English, `b` UK
English, `e` Spanish, `f` French, `h` Hindi, `i` Italian, `j` Japanese, `p`
Brazilian Portuguese, `z` Mandarin).

Best English voices (hexgrad grades): **`af_heart` (A, default)**,
`af_bella` (A-), `bf_emma` (B-), `am_michael`/`am_fenrir`/`am_puck` (C+).

Non-English voices require the matching misaki extra installed (e.g.
`uv pip install "misaki[ja]"` for Japanese, `misaki[zh]` for Mandarin). English
works out of the box — `misaki[en]` ships prebuilt espeakng-loader wheels, so no
system espeak-ng is needed.

## HTTP API

| Method & path | Purpose |
|---|---|
| `POST /v1/audio/speech` | `{model, input, voice, response_format, speed?}` → audio bytes. `response_format`: `mp3` (default) or `wav`. Returns `audio/mpeg` / `audio/wav`. |
| `GET /v1/models` | OpenAI-shaped list containing `kokoro`. |
| `GET /v1/audio/voices` | `{"voices": [...]}` (Kokoro-FastAPI-compatible). |
| `GET /health` | `{"status": "ok", "device": "mps\|cuda\|cpu"}`. |

No authentication: any `Authorization` header is ignored (lecturn's OpenAI SDK
sends a placeholder Bearer).

## Devices

The server auto-selects the best device: **CUDA → MPS (Apple Silicon) → CPU**.
On Apple Silicon it sets `PYTORCH_ENABLE_MPS_FALLBACK=1` automatically (some
Kokoro ops fall back to CPU). `GET /health` reports the selected device.

## Notes & limits

- Output is 24 kHz mono; MP3 is encoded via ffmpeg (real ID3/frame-sync bytes,
  which lecturn's resume cache validates).
- KPipeline silently **truncates any single unbreakable segment longer than 510
  phoneme tokens**. lecturn chunks input to ≤1000 characters, well under this in
  practice, so v1 does not add its own re-chunker.

## Troubleshooting

- **`ffmpeg not found` / MP3 export fails** — install ffmpeg
  (`brew install ffmpeg` / `apt install ffmpeg`) and ensure it's on `PATH`.
- **English pronunciation is wrong / espeak errors** — `misaki[en]` bundles
  espeakng-loader, but if it can't load, install the system library as a
  fallback: `brew install espeak-ng` (macOS) or `apt install espeak-ng` (Linux).
- **First request hangs for minutes** — that's the one-time weight download;
  watch the logs. Use `--warm` to do it at startup instead.
- **Slow on CPU** — expected; a GPU (CUDA) or Apple Silicon (MPS) is much
  faster. Kokoro is small, so CPU is still usable for short runs.
- **Air-gapped / fully offline use** — even with weights cached, the kokoro
  library HEAD-checks huggingface.co for freshness on each model load. Set
  `HF_HUB_OFFLINE=1` to skip the check and run with no network at all.

## Alternatives (also work with `--provider local`)

Because the endpoint shape is the standard OpenAI TTS API, lecturn's local
provider works with community Kokoro servers too — just point `--base-url` at
them:

- **[remsky/Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI)** — a
  more full-featured Docker image with CUDA/ROCm builds and streaming.
- **[Blaizzy/mlx-audio](https://github.com/Blaizzy/mlx-audio)** — an Apple MLX
  backend, often faster than PyTorch-MPS on Apple Silicon.

This server aims to be the minimal, zero-config option that ships in-repo.
