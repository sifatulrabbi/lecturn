# CLAUDE.md — guidance for AI agents working in this repo

`lecturn` (package `textbook_audiobook`) is a Python 3.12+ CLI that converts
textbooks (PDF/EPUB/TXT/MD) into narrated MP3 audiobooks via a pluggable TTS
**provider** — currently StepFun and OpenRouter (both OpenAI-SDK-compatible).
Managed with `uv`.

Human docs: [docs/SETUP.md](docs/SETUP.md) · [docs/USAGE.md](docs/USAGE.md) ·
[docs/DEV.md](docs/DEV.md). Read `docs/DEV.md` (esp. the **Providers** section)
for architecture and invariants before changing pipeline/TTS/provider code.

## ⚠️ Operating rules (read first)

- **Never start a live audio-generation run without the user's explicit
  consent.** Any `lecturn convert` *without* `--dry-run`, or any script that
  calls a provider's TTS API, spends real money and consumes tight per-model rate
  limits (StepFun: 5 concurrent / 10 RPM). Ask first, every time — even when it
  seems like the obvious next step.
- `--provider` is **required** on `convert`, `list-models`, and `list-voices`.
- Safe to run anytime (network-free, no key): `--dry-run`, `lecturn
  list-providers`, `lecturn list-models -p <name>`, `lecturn list-voices -p
  <name>`, and `uv run pytest`.
- Free key/model checks without spending TTS quota: StepFun `GET /v1/models`;
  OpenRouter `GET /api/v1/models?output_modalities=speech`.

## Commands

```bash
uv sync --extra dev          # dev environment (runtime + test deps)
uv run pytest                # full suite — network-free, no API key needed
uv run lecturn --help        # run CLI from source
uv tool install . --reinstall  # update the globally-installed `lecturn` command
```

Requires **ffmpeg** on `PATH` (used by pydub, and by the audio-path tests).

## Testing invariant

The test suite must stay **network-free and key-free**. The transport
(`TTSClient._build_client` / `_request_audio`) is stubbed to return real
ffmpeg-encoded MP3 bytes (`tests/test_pipeline.py`'s `stub_network`;
`tests/conftest.py` supplies the bytes). Any new network behaviour must be
covered by a stubbed test — never make tests depend on a live API. Provider
metadata/catalogues/key-resolution are unit-tested in `tests/test_providers.py`
with monkeypatched env.

## Provider gotchas (non-obvious, cost real time to rediscover)

- **The transport is shared.** Both providers are OpenAI-SDK-compatible, so one
  generic `TTSClient` drives them; only the `Provider` (base URL, catalogues,
  char cap, error advice) differs. A non-OpenAI provider would need its own
  transport at the `_build_client`/`_request_audio` seam.
- **StepFun — voice access is per-account.** A valid catalogue voice can still
  return `voice_id_invalid`. English-keyed voices (e.g. `lively-girl`, the
  default) are the most broadly available.
- **StepFun — quota is per-model.** `stepaudio-2.5-tts` (premium) can be out of
  quota (`402`) while `step-tts-2` (economy) works. A `402` is returned *before*
  voice validation, so during a quota outage every voice appears to fail — that's
  not a voice bug. `step-tts-mini` is **not** a live model.
- **OpenRouter — voices are model-specific.** A voice valid for one model is
  rejected by another, so automatic model fallback is **off by default** there (a
  silent model swap would break the voice). Model IDs are namespaced
  (`openai/gpt-4o-mini-tts`). Pricing is per-token, so catalogue
  `price_per_10k_chars` values are approximations for the estimate only.

## Invariants not to break

- Chunks never exceed the selected provider's `hard_char_limit` (StepFun 1000,
  OpenRouter 2000). The chunker guarantees `≤ max_chars`; the CLI caps
  `--max-chars` at the provider limit and `chunk_document(..., hard_limit=…)`
  enforces it defensively.
- Synthesis is sequential by default at the library level
  (`pipeline.DEFAULT_CONCURRENCY = 1`); concurrency is bounded and paired with an
  RPM throttle so it can't exceed account limits. Concurrency changes wall-clock
  time only, never total cost.
- Chunk writes are atomic; the resume cache is fingerprinted by
  **provider**+voice+response_format+text (NOT model — a book can span models via
  the fallback) and validated by MP3 magic bytes (provider MP3s can be ID3-prefixed
  and report mutagen length 0, so don't gate on decoded length). `--no-resume`
  forces a full regenerate. Don't regress to non-atomic writes, size-only cache
  checks, a model-keyed fingerprint, or dropping provider from the key.
- Keep secrets out of code — read the key from the environment (each provider
  declares its own `api_key_env`).

## Conventions

- Branch off `main`; keep commits scoped and self-consistent (tests green at each
  commit). Update the relevant `docs/*.md` in the same change.
- This repo uses a `Co-Authored-By` trailer on AI-assisted commits.
- Match surrounding style: `from __future__ import annotations`, type hints,
  module docstrings that explain the "why".
