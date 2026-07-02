# lecturn — Development Guide

For contributors and maintainers. For installing/using the CLI, see
[SETUP.md](SETUP.md) and [USAGE.md](USAGE.md).

---

## Dev setup

```bash
uv sync --extra dev        # .venv with runtime + test dependencies
uv run pytest              # run the test suite
uv run lecturn --help      # run the CLI from source (no install)
```

Requires **ffmpeg** on `PATH` (same as the tool itself) — the audio-path tests
encode/decode real MP3s.

---

## Repository layout

```
src/textbook_audiobook/
├── cli.py            # Typer CLI: convert, list-providers, list-voices, list-models
├── pipeline.py       # Orchestration: load → clean → chunk → synthesize → assemble
│                     #   + bounded concurrency, RPM throttle, fingerprinted resume cache
├── providers/        # TTS provider abstraction (see "Providers" below)
│   ├── __init__.py   # registry: get_provider(), available_providers()
│   ├── base.py       # Provider ABC, ModelInfo, shared error heuristics, key resolution
│   ├── stepfun.py    # StepFunProvider: catalogue, pricing, advice, char cap 1000
│   └── openrouter.py # OpenRouterProvider: curated catalogue, char cap 2000
├── loaders/
│   ├── __init__.py   # dispatch by file extension
│   ├── base.py       # LoaderError + Loader protocol
│   ├── pdf_loader.py     # PyMuPDF; chapters from TOC; flags image-only PDFs
│   ├── epub_loader.py    # ebooklib + BeautifulSoup; chapters from spine
│   ├── markdown_loader.py# #/## headings → chapters
│   └── text_loader.py    # `---` delimiter → chapters
├── cleaner.py        # strip page numbers / running headers / URLs; fix hyphenation
├── chunker.py        # ≤ provider-cap sentence-boundary chunking; never crosses chapters
├── tts.py            # Generic TTSClient (OpenAI SDK): retries, error tiering, fallback,
│                     #   atomic writes, thread-safe stats — driven by any Provider
├── assembler.py      # pydub concat (single / per-chapter) + mutagen ID3 tags
├── config.py         # TTSConfig (provider + resolved connection fields) + key resolution
├── models.py         # Document / Chapter / Chunk dataclasses + slugify
└── __main__.py       # `python -m textbook_audiobook`

tests/                # pytest suite (network-free; see below)
docs/                 # SETUP.md, USAGE.md, DEV.md
PLAN.md               # original design spec
```

---

## Pipeline stages

1. **Load** (`loaders/`) — one loader per format produces a `Document` of
   `Chapter`s. Loaders preserve structure only; they don't clean.
2. **Clean** (`cleaner.py`) — per chapter: strip page numbers, running
   headers/footers, bare URLs; repair PDF hyphenation; normalise whitespace.
   Never merges or drops chapters (except empties).
3. **Chunk** (`chunker.py`) — split each chapter into `≤ max_chars` (≤1000)
   pieces on sentence boundaries; never cross chapter boundaries. Oversized
   sentences fall back to clause → word → hard-cut splitting.
4. **Synthesize** (`tts.py` via `pipeline.py`) — one `POST /v1/audio/speech` per
   chunk against the selected provider, full MP3 written to disk atomically.
   Bounded concurrency + RPM throttle. Per-chunk cache enables resume.
5. **Assemble** (`assembler.py`) — pydub/ffmpeg concatenation into a single file
   or per-chapter files; mutagen writes ID3v2 tags.

---

## Providers

`lecturn` is multi-provider. A `Provider`
(`providers/base.py`) captures everything that differs between TTS services:
base URL, API-key env vars, the model/voice catalogues (with pricing), the
per-request character cap, concurrency/RPM guidance, and the wording of error
advice (`Provider.explain`). Concrete providers (`stepfun.py`, `openrouter.py`)
are registered as singletons in `providers/__init__.py`; `--provider <name>`
resolves through `get_provider()`.

The **transport is provider-agnostic**: every supported provider is
OpenAI-SDK-compatible (`client.audio.speech.create(...)` returning a full audio
body), so the single generic `TTSClient` in `tts.py` drives all of them. The
`TTSConfig` (`config.py`) carries the chosen provider plus the resolved
connection fields; the client delegates the per-request char cap
(`config.hard_char_limit`) and error advice (`provider.explain`) to it. Error
*classification* (`is_quota_error` / `is_voice_error` / `retry_after` in
`base.py`) is shared because the OpenAI SDK exception taxonomy is the same for
all providers.

**To add a provider:** implement a `Provider` subclass and add an instance to
`_PROVIDERS`. If it is OpenAI-SDK-compatible, no client changes are needed. If it
is *not*, that is the one place a bespoke transport would be introduced (the
`TTSClient._build_client` / `_request_audio` seam) — everything else (chunking,
cache, assembly, CLI) stays the same.

Provider-specific facts worth knowing:

- **StepFun** — char cap 1000; voices are shared across its two models, so the
  premium→economy fallback (`step-tts-2`) is on by default. Voice access is
  per-account.
- **OpenRouter** — char cap 2000; namespaced model IDs (`openai/gpt-4o-mini-tts`,
  …). **Voices are model-specific**, so automatic fallback is *off* by default (a
  silent model swap would break the voice). Pricing is per-token, so its catalogue
  `price_per_10k_chars` values are best-effort approximations for the estimate
  only. The live list is free to confirm:
  `GET /api/v1/models?output_modalities=speech`.

---

## Testing

```bash
uv run pytest              # whole suite
uv run pytest -q           # quiet
uv run pytest tests/test_pipeline.py -k resume    # a subset
```

The suite is **network-free and needs no API key** — this is a hard invariant.
The transport (`TTSClient._build_client` / `TTSClient._request_audio`) is stubbed
to return **real ffmpeg-encoded MP3 bytes** (see `tests/test_pipeline.py`'s
`stub_network` fixture + `tests/conftest.py`), so everything except the actual
HTTP call runs for real: load → clean → chunk → synthesize (stubbed) → assemble
(pydub/ffmpeg) → ID3 tagging. `tests/test_providers.py` covers the registry,
catalogues, key resolution, and cost/advice per provider — all with monkeypatched
env, no live calls.

Coverage highlights:

- **Loaders** — Markdown, plain text, PDF (TOC→chapters, metadata, image-only
  detection), EPUB (fixtures generated on the fly with `fitz` / `ebooklib`).
- **Chunker / cleaner** — the deterministic core.
- **Assembler** — real concat + ID3 readback (single-file and per-chapter tracks).
- **TTS client** — retry/backoff, error classification, model fallback, atomic
  write leaves no partial file on failure.
- **Pipeline** — end-to-end; resume (skip cached), `--no-resume`, fingerprint
  invalidation on voice change, cache reuse across model switch, corrupt-cache
  rejection, concurrency is bounded, the
  RPM rate limiter, and the sequential (`concurrency=1`) guarantee.
- **CLI** — argument validation, dry-run, catalogue commands, version.

> If you add a feature that touches the network path, stub it — never make the
> suite depend on a live API or key.

---

## Key design decisions & invariants

- **OpenAI-SDK-compatible providers** (StepFun `…/v1`, OpenRouter
  `…/api/v1`, …) driven by one generic `TTSClient`. No streaming — each chunk is
  one full-file synth call.
- **Per-provider hard char cap** (`provider.hard_char_limit`: StepFun 1000,
  OpenRouter 2000). The chunker guarantees no chunk exceeds the effective cap;
  `--max-chars` defaults to it and can only lower it (the CLI rejects a larger
  value, and `chunk_document(..., hard_limit=…)` enforces it defensively).
- **Synthesis defaults to sequential** at the library level
  (`pipeline.DEFAULT_CONCURRENCY = 1`); the CLI raises it to 3. Concurrency is
  bounded and paired with an RPM throttle so it can't exceed account rate limits.
  Concurrency changes wall-clock time only — never total cost.
- **Atomic chunk writes** (`tts._atomic_write_bytes`: temp + fsync + `os.replace`)
  so an interrupt never leaves a partial cache file.
- **Fingerprinted resume cache** — cache filenames embed a hash of
  provider+voice+response_format+text (deliberately **not** the model, so a book
  can span models — e.g. the premium→economy fallback — without invalidating the
  cache). Provider **is** in the key: a voice ID like `alloy` could exist under
  more than one provider yet produce different audio. A provider, voice, or text
  change invalidates stale audio; `--no-resume` forces a full regenerate. Files
  are validated as real MP3s before reuse.
- **Tiered error handling** in `tts.py`:
  - `429` / timeout / `5xx` → retry with exponential backoff (honours
    `Retry-After`).
  - quota (`402`) / unknown-model (`400`/`404`) → fail fast, fallback-eligible
    (auto-retry once on `--fallback-model`).
  - bad voice → fail fast, **not** fallback-eligible (a model swap can't fix it).
  - auth (`401`) → fail fast, never falls back.
- **`TTSClient` is thread-safe** — a lock guards `stats` and the
  `_active_model` fallback flip so one client can drive concurrent workers.

### StepFun account gotchas (learned the hard way)

- **Voice access is per-account.** A valid catalogue voice can still return
  `voice_id_invalid`. English-keyed voices (e.g. `lively-girl`) are the most
  broadly available; the default is `lively-girl`.
- **Quota is per-model.** `stepaudio-2.5-tts` can be out of quota (`402`) while
  `step-tts-2` works. A `402` is returned *before* voice validation, so during a
  quota outage every voice appears to fail — don't mistake that for a voice bug.
- `GET /v1/models` (HTTP 200) is a free way to confirm the key is valid and list
  live models without spending TTS quota. `step-tts-mini` is **not** a live
  model and was removed from the catalogue.

---

## Coding conventions

- Match the surrounding style: `from __future__ import annotations`, type hints,
  small focused functions, module docstrings explaining the "why".
- Keep secrets out of code — read from the environment (`config.resolve_api_key`).
- New network behaviour must be covered by a **stubbed** test.
- Prefer clear names and comments that explain intent over cleverness.

---

## Contributing workflow

```bash
git checkout -b my-change            # branch off main
# ... edit, add tests ...
uv run pytest                        # keep the suite green
git commit                           # focused, well-described commits
git push -u origin my-change
```

- Branch off `main`; keep commits scoped and self-consistent (tests pass at each).
- Update the relevant doc (`USAGE.md` for user-facing flags, this file for
  internals) in the same change.
- Reinstall the global command after changes if you use it: `uv tool install .
  --reinstall`.

---

## Release / distribute

- Version lives in `pyproject.toml` (`[project].version`) and is surfaced by
  `lecturn --version`.
- `uv.lock` is currently git-ignored; commit it if you want fully reproducible
  installs.
- Build artifacts with `uv build` (hatchling backend, wheel packages
  `src/textbook_audiobook`).
