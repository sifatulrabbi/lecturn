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
├── cli.py            # Typer CLI: convert, list-voices, list-models
├── pipeline.py       # Orchestration: load → clean → chunk → synthesize → assemble
│                     #   + bounded concurrency, RPM throttle, fingerprinted resume cache
├── loaders/
│   ├── __init__.py   # dispatch by file extension
│   ├── base.py       # LoaderError + Loader protocol
│   ├── pdf_loader.py     # PyMuPDF; chapters from TOC; flags image-only PDFs
│   ├── epub_loader.py    # ebooklib + BeautifulSoup; chapters from spine
│   ├── markdown_loader.py# #/## headings → chapters
│   └── text_loader.py    # `---` delimiter → chapters
├── cleaner.py        # strip page numbers / running headers / URLs; fix hyphenation
├── chunker.py        # ≤1000-char sentence-boundary chunking; never crosses chapters
├── tts.py            # TTS clients (OpenAI SDK): _BaseTTSClient (retries, error
│                     #   tiering, fallback, atomic writes, thread-safe stats) +
│                     #   StepFunTTSClient / OpenRouterTTSClient subclasses
├── assembler.py      # pydub concat (single / per-chapter) + mutagen ID3 tags
├── config.py         # constants, model pricing, voice catalogues, env-key
│                     #   resolution; StepFunConfig / OpenRouterConfig (TTSConfig union)
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
   chunk, full MP3 written to disk atomically. Bounded concurrency + RPM
   throttle. Per-chunk cache enables resume.
5. **Assemble** (`assembler.py`) — pydub/ffmpeg concatenation into a single file
   or per-chapter files; mutagen writes ID3v2 tags.

---

## Testing

```bash
uv run pytest              # whole suite
uv run pytest -q           # quiet
uv run pytest tests/test_pipeline.py -k resume    # a subset
```

The suite is **network-free and needs no API key** — this is a hard invariant.
The StepFun transport (`StepFunTTSClient._request_audio`) is stubbed to return
**real ffmpeg-encoded MP3 bytes** (see `tests/conftest.py`), so everything except
the actual HTTP call runs for real: load → clean → chunk → synthesize (stubbed)
→ assemble (pydub/ffmpeg) → ID3 tagging.

Coverage highlights:

- **Loaders** — Markdown, plain text, PDF (TOC→chapters, metadata, image-only
  detection), EPUB (fixtures generated on the fly with `fitz` / `ebooklib`).
- **Chunker / cleaner** — the deterministic core.
- **Assembler** — real concat + ID3 readback (single-file and per-chapter tracks).
- **TTS client** — retry/backoff, error classification, model fallback, atomic
  write leaves no partial file on failure. OpenRouter
  (`test_tts_openrouter.py`): its error guidance, no-fallback default, and that
  the real request always sends `response_format="mp3"`.
- **Pipeline** — end-to-end; resume (skip cached), `--no-resume`, fingerprint
  invalidation on voice change, cache reuse across model switch, corrupt-cache
  rejection, concurrency is bounded, the RPM rate limiter, and the sequential
  (`concurrency=1`) guarantee. The OpenRouter `client_factory` seam and its
  cross-run cache hits (`test_pipeline_openrouter.py`).
- **CLI** — argument validation, dry-run, catalogue commands (grouped + the
  `--provider` filter), per-provider defaults, version.

> If you add a feature that touches the network path, stub it — never make the
> suite depend on a live API or key.

---

## Provider architecture (StepFun + OpenRouter)

Two TTS providers are supported, selected by the CLI's `--provider` flag
(`stepfun`, the default, or `openrouter`). Both expose the same OpenAI-compatible
`POST /audio/speech` shape, so almost everything is shared:

- **One transport, two thin subclasses.** `tts._BaseTTSClient` holds all the
  machinery (retry/backoff, `Retry-After`, model fallback, atomic writes,
  OpenAI-SDK error mapping, thread-safe stats). `StepFunTTSClient` and
  `OpenRouterTTSClient` only override `_explain()` for account-specific error
  guidance. The config is duck-typed (`.api_key` / `.base_url` / `.model` /
  `.voice` / `.response_format`), so either provider's config flows through.
- **Two configs, one union.** `config.StepFunConfig` and `config.OpenRouterConfig`
  each resolve their own key/base-URL from the environment
  (`STEPFUN_API_KEY` / `OPENROUTER_API_KEY`, `*_BASE_URL`). `config.TTSConfig` is
  their union — the type used wherever "a config for either provider" is meant.
- **Pipeline seam.** `pipeline.run_pipeline(..., client_factory=None)` builds a
  `StepFunTTSClient` by default (unchanged StepFun path); the CLI passes a
  `client_factory` returning an `OpenRouterTTSClient` for `--provider openrouter`.
  `fallback_model` is only consulted by the default StepFun factory.
- **Two catalogues.** `config.MODELS` / `VOICES` (StepFun) and
  `config.OPENROUTER_MODELS` / `KOKORO_VOICES` (Kokoro). `estimate_cost` looks in
  both; `list-models` / `list-voices` print both, grouped, with a `--provider`
  filter.
- **Fingerprint invariant unchanged.** The resume-cache fingerprint stays
  voice+response_format+text (still **not** the model, and **no** provider tag).
  Kokoro voice IDs (`af_heart`, …) are structurally disjoint from StepFun's
  (`lively-girl`, …), so they can never collide — a cache keyed on the voice is
  already provider-safe.
- **OpenRouter PCM gotcha.** OpenRouter's `/audio/speech` defaults to raw **PCM**;
  `OpenRouterConfig.response_format` defaults to `"mp3"` and the client always
  sends it. Never omit it, or MP3 magic-byte cache validation and pydub stitching
  break. `test_tts_openrouter.py` guards this on the real request kwargs.
- **No OpenRouter fallback.** Kokoro is the only OpenRouter model, so
  `OpenRouterTTSClient.fallback_model` defaults to `None` (fallback stays a
  StepFun-tier concept).

## Key design decisions & invariants

- **Providers via the OpenAI SDK** — StepFun at `https://api.stepfun.ai/v1`,
  OpenRouter at `https://openrouter.ai/api/v1`. No streaming — each chunk is one
  full-file synth call.
- **1000-char hard cap** per request (`config.HARD_CHAR_LIMIT`). The chunker
  guarantees no chunk exceeds it; `--max-chars` can only lower it.
- **Synthesis defaults to sequential** at the library level
  (`pipeline.DEFAULT_CONCURRENCY = 1`); the CLI raises it to 3. Concurrency is
  bounded and paired with an RPM throttle so it can't exceed account rate limits.
  Concurrency changes wall-clock time only — never total cost.
- **Atomic chunk writes** (`tts._atomic_write_bytes`: temp + fsync + `os.replace`)
  so an interrupt never leaves a partial cache file.
- **Fingerprinted resume cache** — cache filenames embed a hash of
  voice+response_format+text (deliberately **not** the model, so a book can span
  models — e.g. the premium→economy fallback — without invalidating the cache).
  A voice or text change invalidates stale audio; `--no-resume` forces a full
  regenerate. Files are validated as real MP3s before reuse.
- **Tiered error handling** in `tts.py`:
  - `429` / timeout / `5xx` → retry with exponential backoff (honours
    `Retry-After`).
  - quota (`402`) / unknown-model (`400`/`404`) → fail fast, fallback-eligible
    (auto-retry once on `--fallback-model`).
  - bad voice → fail fast, **not** fallback-eligible (a model swap can't fix it).
  - auth (`401`) → fail fast, never falls back.
- **`StepFunTTSClient` is thread-safe** — a lock guards `stats` and the
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
