# CLAUDE.md — guidance for AI agents working in this repo

`lecturn` (package `textbook_audiobook`) is a Python 3.12+ CLI that converts
textbooks (PDF/EPUB/TXT/MD) into narrated MP3 audiobooks via a TTS provider —
StepFun (default), OpenRouter/Kokoro (`--provider openrouter`), or a self-hosted
local Kokoro server (`--provider local`, free/offline; server app lives in
`server/` with its own uv environment). Managed with `uv`.

Human docs: [docs/SETUP.md](docs/SETUP.md) · [docs/USAGE.md](docs/USAGE.md) ·
[docs/DEV.md](docs/DEV.md). Read `docs/DEV.md` for architecture and invariants
before changing pipeline/TTS code.

## ⚠️ Operating rules (read first)

- **Never start a live audio-generation run without the user's explicit
  consent.** Any `lecturn convert` *without* `--dry-run`, or any script that
  calls the StepFun or OpenRouter TTS API, spends real money and (for StepFun)
  consumes tight per-model rate limits (5 concurrent / 10 RPM). Ask first, every
  time — even when it seems like the obvious next step.
- Safe to run anytime (network-free, no key): `--dry-run`, `lecturn list-models`,
  `lecturn list-voices`, and `uv run pytest`.
- `GET /v1/models` (HTTP 200) is a free way to check the key/list live models
  without spending TTS quota.

## Commands

```bash
uv sync --extra dev          # dev environment (runtime + test deps)
uv run pytest                # full suite — network-free, no API key needed
uv run lecturn --help        # run CLI from source
uv tool install . --reinstall  # update the globally-installed `lecturn` command
```

Requires **ffmpeg** on `PATH` (used by pydub, and by the audio-path tests).

## Testing invariant

The test suite must stay **network-free and key-free**. The StepFun transport is
stubbed to return real ffmpeg-encoded MP3 bytes (`tests/conftest.py`). Any new
network behaviour must be covered by a stubbed test — never make tests depend on
a live API. Note: `tokens.count_tokens` downloads a tiktoken encoding on first
use, so an autouse `conftest.py` fixture forces its offline heuristic; tests that
exercise the real counting path inject a fake encoding instead of downloading.

## StepFun gotchas (non-obvious, cost real time to rediscover)

- **Voice access is per-account.** A valid catalogue voice can still return
  `voice_id_invalid` ("you do not have access to it"). English-keyed voices
  (e.g. `lively-girl`, the default) are the most broadly available.
- **Quota is per-model.** `stepaudio-2.5-tts` (premium) can be out of quota
  (`402`) while `step-tts-2` (economy) works. A `402` is returned *before* voice
  validation, so during a quota outage every voice appears to fail — that's not a
  voice bug. If premium is dry, use `--model step-tts-2`.
- `step-tts-mini` is **not** a live model (absent from `GET /v1/models`).

## OpenRouter gotchas

- **OpenRouter's `/audio/speech` defaults to `response_format: pcm`**, not mp3.
  lecturn always sends `mp3` explicitly — never omit it, or the MP3
  magic-byte cache validation and pydub stitching break.
- Kokoro voice IDs are namespaced by language/gender prefix (`af_`, `am_`,
  `bf_`, `bm_`, …); quality varies a lot — `af_heart` (A grade, the default)
  and `af_bella` (A-) are the best English voices.
- Kokoro caps input at **4096 tokens** per request; enforced by
  `OpenRouterTTSClient._check_input_limits` (via `tokens.count_tokens`) — inert
  under the 1000-char chunk cap, but keep it correct.

## Local provider (`--provider local`)

- Talks to any OpenAI-compatible Kokoro server at `LOCAL_TTS_BASE_URL`
  (default `http://127.0.0.1:8880/v1`) — ours in `server/`, or community ones
  (remsky/Kokoro-FastAPI, mlx-audio). Model id `kokoro`, voice `af_heart`,
  same Kokoro voice catalogue and 4096-token guard as OpenRouter.
- **No API key**: `LocalConfig.from_env` never raises `MissingApiKeyError`; it
  defaults the key to the placeholder `"local"` (the OpenAI SDK needs a
  non-empty string). Optional `LOCAL_TTS_API_KEY` overrides it.
- **Local primary defaults to NO fallback** (`--fallback-model none`
  semantics) — a dead local server must not silently start spending OpenRouter
  money. An explicit `--fallback-model` re-enables the OpenRouter fallback.
- Local synthesis is free (no paid API), but the "ask before live runs" rule
  above still covers StepFun/OpenRouter — including a local run's *explicit*
  fallback, which can spend real money if it fires.
- The `server/` app is a **separate uv project** (`requires-python <3.13`
  because `kokoro` 0.9.4 pins it; torch stays out of lecturn's root env).
  Its tests are offline: `cd server && uv sync --extra dev && uv run pytest`.
  First real synthesis downloads ~327 MB of weights from HF Hub.

## Cross-provider fallback

- On a fallback-eligible failure (quota/unknown-model) the run switches, **once
  and one-way**, to OpenRouter + `hexgrad/kokoro-82m` (voice `af_heart`) — the
  target for **both** providers (StepFun's old premium→economy default is
  retired). `FallbackTTSClient` (in `tts.py`) wraps a primary + a lazily-built
  fallback; a StepFun-only run needs no `OPENROUTER_API_KEY` until the fallback
  actually fires, at which point a missing key surfaces the original error plus a
  skip note. `--fallback-model none` disables it; an OpenRouter primary's default
  fallback equals its model and is a no-op. A **local** primary defaults to no
  fallback at all (see the local-provider section above).

## Invariants not to break

- Chunks never exceed `config.HARD_CHAR_LIMIT` (1000 chars) — StepFun's hard cap.
- Synthesis is sequential by default at the library level
  (`pipeline.DEFAULT_CONCURRENCY = 1`); concurrency is bounded and paired with an
  RPM throttle so it can't exceed account limits. Concurrency changes wall-clock
  time only, never total cost.
- Chunk writes are atomic; the resume cache is fingerprinted by
  voice+response_format+text (NOT model, NOT provider — a book can span models
  **and providers** via the fallback), computed from `client.active_config` at
  dispatch so post-fallback chunks are keyed on the fallback voice, and validated
  by MP3 magic bytes (StepFun MP3s are ID3-prefixed and report mutagen length 0,
  so don't gate on decoded length). `--no-resume` forces a full regenerate. Don't
  regress to non-atomic writes, size-only cache checks, or a model/provider-keyed
  fingerprint.
- Keep secrets out of code — read the key from the environment.

## Conventions

- Branch off `main`; keep commits scoped and self-consistent (tests green at each
  commit). Update the relevant `docs/*.md` in the same change.
- This repo uses a `Co-Authored-By` trailer on AI-assisted commits.
- Match surrounding style: `from __future__ import annotations`, type hints,
  module docstrings that explain the "why".
