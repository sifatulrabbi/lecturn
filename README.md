# Textbook → Audiobook

Convert textbooks from **PDF, EPUB, plain text, or Markdown** into narrated
audiobooks using [StepFun's](https://platform.stepfun.ai) TTS API.

The tool runs the full pipeline locally and synchronously:

```
load → clean → chunk → synthesize (StepFun TTS) → assemble (MP3 + ID3)
```

## Requirements

- **Python 3.12+**
- [`uv`](https://docs.astral.sh/uv/) for package management
- **ffmpeg** on your `PATH` (required by `pydub` for MP3 concatenation)
  - macOS: `brew install ffmpeg`
- A StepFun API key

## Install

```bash
uv sync
```

This creates a `.venv` with all dependencies pinned from `pyproject.toml`.

## Configure credentials

Set your API key in the environment (never commit it):

```bash
export STEPFUN_API_KEY="sk-..."        # or STEPFUN_STEP_PLAN_API_KEY
# optional override:
export STEPFUN_BASE_URL="https://api.stepfun.ai/v1"
```

## Usage

```bash
# Single-file audiobook (best-quality default model)
uv run textbook-audiobook convert book.pdf -o output/

# Economy model, one MP3 per chapter
uv run textbook-audiobook convert book.epub --model step-tts-2 --split-by-chapter

# Estimate cost without calling the API
uv run textbook-audiobook convert book.md --dry-run

# Inspect catalogues
uv run textbook-audiobook list-models
uv run textbook-audiobook list-voices
```

You can also run it as a module: `uv run python -m textbook_audiobook ...`.

### Key options

| Option | Default | Notes |
| --- | --- | --- |
| `--model, -m` | `stepaudio-2.5-tts` | Best quality. `step-tts-2` is the economy fallback. |
| `--fallback-model` | `step-tts-2` | Retried automatically if the primary model is rejected (quota/entitlement/unknown-model). `none` disables it. Not used for auth errors. |
| `--voice` | `lively-girl` | A StepFun voice ID (not an OpenAI name). See `list-voices`. Voice access is per-account; if one is rejected, try another (English-keyed voices are the most widely available). |
| `--output, -o` | `output/` | Output directory. |
| `--split-by-chapter` | off | One MP3 per chapter with track numbers. |
| `--max-chars` | `1000` | Hard StepFun cap; cannot be exceeded. |
| `--dry-run` | off | Plan + cost estimate, no synthesis. |
| `--no-resume` | off | Ignore cached chunk audio and re-synthesize. |
| `-y, --yes` | off | Skip the cost confirmation prompt. |

## How it works

- **Loaders** (`loaders/`): one per format. PDF uses PyMuPDF (text-layer only;
  image-only PDFs are flagged for a future OCR pass). EPUB uses `ebooklib` +
  BeautifulSoup. Markdown treats `#`/`##` as chapter markers. Plain text splits
  on `---` delimiters.
- **Cleaner** (`cleaner.py`): strips page numbers, running headers/footers, and
  bare URLs; repairs PDF hyphenation; normalises whitespace — per chapter, so
  chapter boundaries survive.
- **Chunker** (`chunker.py`): splits each chapter into ≤1000-char chunks on
  sentence boundaries. Chunks never cross chapters. Oversized single sentences
  fall back to clause → word → hard-cut splitting.
- **TTS client** (`tts.py`): OpenAI SDK pointed at StepFun. One
  `POST /v1/audio/speech` per chunk, full MP3 written to disk (no streaming).
  Exponential backoff honouring `Retry-After` on 429s. Tracks usage for cost.
- **Assembler** (`assembler.py`): concatenates chunk MP3s (pydub/ffmpeg) into a
  single file or per-chapter files and writes ID3v2 tags (mutagen).
- **Pipeline** (`pipeline.py`): synchronous orchestration with a Rich progress
  bar. Caches synthesized chunks so interrupted runs resume.

## Development

```bash
uv run pytest
```

Tests are network-free and require no API key. The StepFun transport is stubbed
to return real ffmpeg-encoded MP3 bytes, so the full pipeline — load, clean,
chunk, synthesize (stubbed), assemble, ID3 tag — is exercised end to end, plus:

- **Loaders**: Markdown, plain text, PDF (TOC→chapters, metadata, image-only
  detection), and EPUB (fixtures generated on the fly).
- **Chunker / cleaner**: the deterministic core.
- **Assembler**: real pydub/ffmpeg concatenation and mutagen ID3 readback
  (single-file and per-chapter with track numbers).
- **TTS client**: retry/backoff, error classification, and model fallback.
- **CLI**: argument validation, dry-run, and the catalogue commands.

(ffmpeg must be on `PATH` for the audio-path tests, same as for the tool itself.)

## Scope (v1)

Text-only narration. No SSML, no streaming playback, no multi-voice casting, no
translation, no GUI, no cloud storage. See `PLAN.md` for the full design.
