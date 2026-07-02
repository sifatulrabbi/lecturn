# CLAUDE.md — guidance for AI agents working in this repo

`lecturn` (package `textbook_audiobook`) is a Python 3.12+ CLI that converts
textbooks (PDF/EPUB/TXT/MD) into narrated MP3 audiobooks via StepFun's TTS API.
Managed with `uv`.

Human docs: [docs/SETUP.md](docs/SETUP.md) · [docs/USAGE.md](docs/USAGE.md) ·
[docs/DEV.md](docs/DEV.md). Read `docs/DEV.md` for architecture and invariants
before changing pipeline/TTS code.

## ⚠️ Operating rules (read first)

- **Never start a live audio-generation run without the user's explicit
  consent.** Any `lecturn convert` *without* `--dry-run`, or any script that
  calls the StepFun TTS API, spends real money and consumes tight per-model rate
  limits (5 concurrent / 10 RPM). Ask first, every time — even when it seems like
  the obvious next step.
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
a live API.

## StepFun gotchas (non-obvious, cost real time to rediscover)

- **Voice access is per-account.** A valid catalogue voice can still return
  `voice_id_invalid` ("you do not have access to it"). English-keyed voices
  (e.g. `lively-girl`, the default) are the most broadly available.
- **Quota is per-model.** `stepaudio-2.5-tts` (premium) can be out of quota
  (`402`) while `step-tts-2` (economy) works. A `402` is returned *before* voice
  validation, so during a quota outage every voice appears to fail — that's not a
  voice bug. If premium is dry, use `--model step-tts-2`.
- `step-tts-mini` is **not** a live model (absent from `GET /v1/models`).

## Invariants not to break

- Chunks never exceed `config.HARD_CHAR_LIMIT` (1000 chars) — StepFun's hard cap.
- Synthesis is sequential by default at the library level
  (`pipeline.DEFAULT_CONCURRENCY = 1`); concurrency is bounded and paired with an
  RPM throttle so it can't exceed account limits. Concurrency changes wall-clock
  time only, never total cost.
- Chunk writes are atomic; the resume cache is fingerprinted by
  voice+response_format+text (NOT model — a book can span models via the
  fallback) and validated by MP3 magic bytes (StepFun MP3s are ID3-prefixed and
  report mutagen length 0, so don't gate on decoded length). `--no-resume`
  forces a full regenerate. Don't regress to non-atomic writes, size-only cache
  checks, or a model-keyed fingerprint.
- Keep secrets out of code — read the key from the environment.

## Conventions

- Branch off `main`; keep commits scoped and self-consistent (tests green at each
  commit). Update the relevant `docs/*.md` in the same change.
- This repo uses a `Co-Authored-By` trailer on AI-assisted commits.
- Match surrounding style: `from __future__ import annotations`, type hints,
  module docstrings that explain the "why".
