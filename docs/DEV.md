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
│                     #   tiering, atomic writes, thread-safe stats), the
│                     #   StepFun/OpenRouter subclasses, and FallbackTTSClient
│                     #   (one-way cross-provider fallback wrapper)
├── tokens.py         # token counting (tiktoken + offline heuristic) for the
│                     #   Kokoro input-size guard
├── assembler.py      # pydub concat (single / per-chapter) + mutagen ID3 tags
├── config.py         # constants, model pricing, voice catalogues, env-key
│                     #   resolution; StepFun/OpenRouter/Local configs (TTSConfig union)
├── models.py         # Document / Chapter / Chunk dataclasses + slugify
└── __main__.py       # `python -m textbook_audiobook`

tests/                # pytest suite (network-free; see below)
docs/                 # SETUP.md, USAGE.md, DEV.md
PLAN.md               # original design spec

packages/
└── lecturn-tts-contract/   # shared, zero-dependency single source of truth for
                            #   the Kokoro contract (voice catalogue, model id,
                            #   default voice, host/port/base URL, format + token
                            #   caps). Imported by BOTH the CLI and server/ as an
                            #   in-repo path dependency so they can never drift.

server/                # separate uv project: self-hosted Kokoro TTS server
                       #   (--provider local); its own torch-bound environment
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
- **TTS client** — retry/backoff, error classification, atomic write leaves no
  partial file on failure, the Kokoro token-input guard. OpenRouter
  (`test_tts_openrouter.py`) and local (`test_tts_local.py`): their error
  guidance and that the real request always sends `response_format="mp3"` (both
  Kokoro servers default to PCM). The local suite also checks `_explain` names
  the configured base URL.
- **Cross-provider fallback** (`test_fallback.py`) — `FallbackTTSClient` switches
  once/one-way on a fallback-eligible error, is skipped (with the original error
  surfaced) when `OPENROUTER_API_KEY` is unset, switches exactly once under
  concurrency, and the pipeline fingerprints post-switch chunks with the fallback
  voice.
- **Token counting** (`test_tokens.py`) — the tiktoken path (fake encoding) and
  the offline heuristic; the suite never triggers a live download.
- **Pipeline** — end-to-end; resume (skip cached), `--no-resume`, fingerprint
  invalidation on voice change, cache reuse across model switch, corrupt-cache
  rejection, concurrency is bounded, the RPM rate limiter, and the sequential
  (`concurrency=1`) guarantee. Cache cleanup on full success (removed on success;
  kept via `cleanup_cache=False`; a failed run leaves it; an interrupted run's
  cache is reused by a later successful resume, which then removes it). The
  OpenRouter and local `client_factory` seams and their cross-run cache hits
  (`test_pipeline_openrouter.py`, `test_pipeline_local.py`).
- **CLI** — argument validation, dry-run, catalogue commands (grouped + the
  `--provider` filter), per-provider defaults, version, the credential/factory
  wiring (with `run_pipeline` stubbed), `_resolve_fallback`, and `_format_price`.

> If you add a feature that touches the network path, stub it — never make the
> suite depend on a live API or key. Note: `tokens.count_tokens` fetches a
> tiktoken encoding on first use (a network download), so an autouse conftest
> fixture forces the offline heuristic; token-counting tests inject a fake
> encoding rather than downloading one.

---

## Provider architecture (StepFun + OpenRouter + Local)

Three TTS providers are supported, selected by the CLI's `--provider` flag
(`stepfun`, the default; `openrouter`; or `local`, a self-hosted Kokoro server).
All expose the same OpenAI-compatible `POST /audio/speech` shape, so almost
everything is shared:

- **One transport, thin subclasses.** `tts._BaseTTSClient` holds all the
  machinery (retry/backoff, `Retry-After`, model fallback, atomic writes,
  OpenAI-SDK error mapping, thread-safe stats). `StepFunTTSClient`,
  `OpenRouterTTSClient`, and `LocalTTSClient` mainly override `_explain()` for
  account-specific error guidance (the two Kokoro clients also add the
  token-input guard). The config is duck-typed (`.api_key` / `.base_url` /
  `.model` / `.voice` / `.response_format`), so any provider's config flows
  through. `LocalTTSClient` is a *clone* of `OpenRouterTTSClient` (same Kokoro
  family, same token guard) rather than a subclass — each provider's guard hook
  delegates to the shared module-level `_enforce_token_ceiling` free function
  (rather than one subclassing the other) so the two stay independent — and its
  `_explain` messages name the
  configured base URL, the first thing to check on a self-hosted server.
- **Three configs, one union.** `config.StepFunConfig`, `config.OpenRouterConfig`,
  and `config.LocalConfig` each resolve their own key/base-URL from the
  environment (`STEPFUN_API_KEY` / `OPENROUTER_API_KEY`, `*_BASE_URL`;
  `LOCAL_TTS_API_KEY` / `LOCAL_TTS_BASE_URL`). `config.TTSConfig` is their union.
  **`LocalConfig` is the no-key exception:** local servers are unauthenticated,
  so `from_env` defaults the key to the placeholder `"local"` (the OpenAI SDK
  needs a non-empty string) and **never raises `MissingApiKeyError`**.
- **Pipeline seam.** `pipeline.run_pipeline(..., client_factory=None)` builds a
  `StepFunTTSClient` by default; the CLI passes a `client_factory:
  Callable[[TTSConfig], _BaseTTSClient]` (it receives the config, so the client
  and the config the cache is fingerprinted against can't drift) for
  `--provider openrouter` and `--provider local`. A separate `fallback_factory`
  supplies the lazily-built OpenRouter/Kokoro fallback (see below).
- **Three catalogues.** `config.MODELS` / `VOICES` (StepFun),
  `config.OPENROUTER_MODELS` / `KOKORO_VOICES` (Kokoro), and `config.LOCAL_MODELS`
  / `LOCAL_VOICES` (local). `LOCAL_MODELS` has a single `kokoro` entry priced at
  an explicit `$0.00` (self-hosted, free); `LOCAL_VOICES` is an **alias** of
  `KOKORO_VOICES` (same server model, same voices — not a duplicated table).
  `estimate_cost` looks in all three; `list-models` / `list-voices` print all
  three, grouped, with a `--provider` filter. The Kokoro voice catalogue
  (`KOKORO_VOICES`, all 54 voices), the default voice, the `kokoro` model id, the
  local base URL, and the token cap are **not** declared here anymore — they are
  imported from the shared `lecturn_tts_contract` package (`packages/`), the
  single source of truth both the CLI and `server/` consume, replacing the old
  arrangement where the CLI re-declared only an English subset that drifted from
  the server's full 54.
- **Fingerprint invariant.** The resume-cache fingerprint stays
  voice+response_format+text (still **not** the model, and **no** provider tag).
  Kokoro voice IDs (`af_heart`, …) are structurally disjoint from StepFun's
  (`lively-girl`, …), so they can never collide — a cache keyed on the voice is
  already provider-safe. Each chunk is fingerprinted from `client.active_config`
  **at dispatch time**, so after a cross-provider fallback the remaining chunks
  are keyed on the fallback voice (the config that actually narrated them).
- **OpenRouter PCM gotcha.** OpenRouter's `/audio/speech` defaults to raw **PCM**;
  `OpenRouterConfig.response_format` defaults to `"mp3"` and the client always
  sends it. Never omit it, or MP3 magic-byte cache validation and pydub stitching
  break. `test_tts_openrouter.py` guards this on the real request kwargs.
- **Cross-provider fallback (`FallbackTTSClient`).** On a fallback-eligible
  failure (quota/unknown-model) the run switches, once and one-way, to an
  OpenRouter/Kokoro client (voice `af_heart`) — the target for *both* providers
  (StepFun's old premium→economy default is retired). The wrapper owns a primary
  + a lazily-built fallback (so a StepFun-only run never needs
  `OPENROUTER_API_KEY` upfront), shares its lock and `stats` with both so counters
  don't race during the switch window, and switches exactly once even under
  concurrency. A missing `OPENROUTER_API_KEY` at fallback time surfaces the
  original error plus a skip note. When OpenRouter is the primary the default
  fallback equals its model and is a no-op; `--fallback-model none` disables it.
  - **Local fallback-default exception.** For a `local` primary the fallback
    **defaults to disabled** (`_resolve_fallback(..., default=None)` in the CLI),
    so a stopped local server never silently spends OpenRouter money. An explicit
    `--fallback-model <model>` re-enables the OpenRouter fallback as usual;
    `none` still disables. StepFun/OpenRouter fallback defaults are unchanged.
  - **Accepted edge:** the one chunk *during* which the switch happens is stored
    under the pre-switch (primary-voice) fingerprint; every later chunk uses the
    fallback voice. Documented and deliberately not engineered around.
- **Kokoro token-input guard.** `ModelInfo.max_input_tokens` (4096 for Kokoro,
  including the local `kokoro` model; `None` for StepFun) is enforced by the
  `_check_input_limits` hook on both Kokoro clients (`OpenRouterTTSClient` and
  `LocalTTSClient`) before a request, via `tokens.count_tokens` (tiktoken's
  `o200k_base` as a
  Kokoro approximation, with an offline character heuristic fallback so tests
  never download). With the 1000-char chunk cap it can't fire today — it makes
  the constraint explicit and future-proof.

## Key design decisions & invariants

- **Providers via the OpenAI SDK** — StepFun at `https://api.stepfun.ai/v1`,
  OpenRouter at `https://openrouter.ai/api/v1`, local at
  `http://127.0.0.1:8880/v1` (override with `--base-url` / `LOCAL_TTS_BASE_URL`).
  No streaming — each chunk is one full-file synth call.
- **1000-char hard cap** per request (`config.HARD_CHAR_LIMIT`). The chunker
  guarantees no chunk exceeds it; `--max-chars` can only lower it.
- **Synthesis defaults to sequential** at the library level
  (`pipeline.DEFAULT_CONCURRENCY = 1`); the CLI raises it to 3. Concurrency is
  bounded and paired with an RPM throttle so it can't exceed account rate limits.
  Concurrency changes wall-clock time only — never total cost.
- **Atomic chunk writes** (`tts._atomic_write_bytes`: temp + fsync + `os.replace`)
  so an interrupt never leaves a partial cache file.
- **Fingerprinted resume cache** — cache filenames embed a hash of
  voice+response_format+text (deliberately **not** the model or provider, so a
  book can span models *and providers* — e.g. the StepFun→OpenRouter/Kokoro
  fallback — without invalidating the cache). The hash is computed from
  `client.active_config` at dispatch, so post-fallback chunks are keyed on the
  fallback voice. A voice or text change invalidates stale audio; `--no-resume`
  forces a full regenerate. Files are validated as real MP3s before reuse.
- **Cache cleanup on complete success** — after `run_pipeline` finishes with
  every chunk synthesized AND every output file written (i.e. `assembler.assemble`
  returned without raising), it deletes this run's resume cache. Scoped narrowly
  and safely: it removes only the chunk files this run actually used/wrote — the
  values of the `chunk_files` map, which are the `active_config`-at-dispatch
  fingerprint paths, so post-fallback chunks are keyed on the fallback voice
  (never recomputed from the primary config) — then `rmdir`s the cache dir and its
  `.audiobook_cache` parent **only while empty**. A cache dir shared with another
  book/voice keeps its other content; there is **no** wholesale tree removal
  (`_cleanup_cache` / `_rmdir_if_empty` in `pipeline.py`). Any exception, partial
  failure, or interrupt skips cleanup and leaves the cache intact so `--resume`
  still works, and cleanup failures (e.g. permission errors) only warn — never
  failing an already-produced audiobook. Disabled by `cleanup_cache=False`
  (`run_pipeline`) / `--keep-cache` (CLI); the library default is `True`, matching
  the CLI (cleanup on). Don't make cleanup fire on partial success, key it on the
  primary config, or delete a cache dir wholesale.
- **Tiered error handling** in `tts.py`:
  - `429` / timeout / `5xx` → retry with exponential backoff (honours
    `Retry-After`).
  - quota (`402`) / unknown-model (`400`/`404`) → fail fast, fallback-eligible
    (the `TTSError` carries `fallback_eligible=True`; `FallbackTTSClient` may
    switch provider once).
  - bad voice → fail fast, **not** fallback-eligible (a provider switch can't fix
    it).
  - auth (`401`) → fail fast, never falls back.
- **Clients are thread-safe** — a lock guards `stats` so one client can drive
  concurrent workers; `FallbackTTSClient` shares that lock (and `stats`) across
  the primary and fallback so the switch flip and counter updates never race.

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
- Keep secrets out of code — read from the environment
  (`config.resolve_stepfun_api_key`).
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
- **Lockfile policy (deliberately asymmetric).** `.gitignore` ignores `uv.lock`,
  so the **root CLI** and the **`lecturn-tts-contract`** package do *not* commit a
  lockfile — both resolve from loose bounds so `uv tool install .` stays flexible
  across environments. The **`server/`** package is the exception: its `uv.lock`
  **is** committed, because it pins a heavy, exact ML stack (`kokoro==0.9.4`,
  a `torch` range) whose reproducibility matters and whose resolution is slow —
  contributors should get the same known-good graph. If you want fully
  reproducible CLI installs too, commit the root `uv.lock` (it now has real
  transitive deps); until then the asymmetry is intentional, not an oversight.
- Build artifacts with `uv build` (hatchling backend, wheel packages
  `src/textbook_audiobook`).
