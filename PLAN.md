# Textbook to Audiobook — Project Plan

## Overview

A tool that converts textbooks from PDF, EPUB, plain text, or Markdown into
narrated audiobooks using StepFun's TTS API. The system handles the full
pipeline — extraction, cleanup, intelligent chunking, synthesis, and
audio assembly — into a single or multi-file MP3 output suitable for
podcast-style or chapter-based audiobook consumption.

---

## Scope

- **Inputs**: PDF, EPUB, `.txt`, `.md`
- **Output**: MP3 audio, optionally with per-chapter file splits and
  basic ID3 metadata (title, author, chapter markers)
- **Interface**: CLI tool; library interface is a future consideration
- **Platform**: macOS (primary), Linux-compatible

Out of scope for now: GUI, streaming playback, multi-voice casting,
translation/paraphrasing, DRM-protected content.

---

## High-Level Architecture

```
┌──────────────┐    ┌─────────────────┐    ┌──────────────────┐
│  Input Files │───▶│  Format Loader  │───▶│  Raw Text (raw)  │
│  PDF/EPUB/   │    │  (per format)   │    └────────┬─────────┘
│  TXT / MD    │    └─────────────────┘             │
└──────────────┘                                    ▼
                                           ┌──────────────────┐
                                           │  Text Cleaner    │
                                           │  - strip headers │
                                           │  - normalise     │
                                           │  - detect breaks │
                                           └────────┬─────────┘
                                                    ▼
                                           ┌──────────────────┐
                                           │  Chunking Engine │
                                           │  - sentence-safe │
                                           │  - chapter-aware │
                                           │  - respects TTS  │
                                           │    token limits  │
                                           └────────┬─────────┘
                                                    ▼
                                           ┌──────────────────┐
                                           │  StepFun TTS API │
                                           │  (step-tts-*)    │
                                           └────────┬─────────┘
                                                    ▼
                                           ┌──────────────────┐
                                           │  Audio Assembler │
                                           │  - concat MP3s   │
                                           │  - chapter split │
                                           │  - ID3 tags      │
                                           └────────┬─────────┘
                                                    ▼
                                           ┌──────────────────┐
                                           │  Output Files    │
                                           │  (single/chap)   │
                                           └──────────────────┘
```

### Component Responsibilities

- **Format Loader**: One module per input type. Extracts plain text from
  the source, preserving structural cues (chapter headings, section
  breaks) as markers.
- **Text Cleaner**: Removes boilerplate (page numbers, running headers,
  URL footers), normalises whitespace and punctuation, and tags
  structural boundaries so the chunker and audio assembler can use them.
- **Chunking Engine**: Splits cleaned text into chunks of at most
  1 000 characters (hard API limit). Always splits on sentence
  boundaries to preserve context. Respects chapter markers so splits
  never land mid-chapter unless a single chapter exceeds 1 000 chars.
- **StepFun TTS Client**: Uses the OpenAI Python SDK pointed at
  `https://api.stepfun.ai/v1` (StepPlan path is a separate concern, not
  used here). Calls `POST /v1/audio/speech` per chunk. Handles retries,
  rate-limit backoff, and writes the complete response to disk per chunk.
  No streaming — each call returns a full MP3 file.
- **Audio Assembler**: Concatenates per-chunk MP3s into final output.
  Optionally splits on chapter boundaries into separate files. Writes
  ID3 metadata.

---

## Input Format Strategy

- **PDF**: `pymupdf` (fitz) for text-layer PDFs; flag image-only PDFs for
  a future OCR pass rather than attempting OCR in v1
- **EPUB**: `ebooklib` or direct zip + HTML parse; strip XML tags, keep
  structural headings as chapter markers
- **Markdown**: Direct file read; treat `#` / `##` headings as chapter
  markers
- **Plain text**: Direct file read; require manual chapter delimiter
  (e.g. `---` or configurable blank-line rules)

---

## StepFun TTS Integration

### Endpoints

- **Primary**: `POST https://api.stepfun.ai/v1/audio/speech`
- **Step Plan variant**: `POST https://api.stepfun.ai/step_plan/v1/audio/speech`
- **Streaming**: An older session note references a `/v1/audio/speech/stream`
  endpoint, but this has **not been confirmed** from current docs or a live
  integration. Flag as unverified until a live test call or doc page confirms it.

### Available Models (confirmed from live integration)

- `stepaudio-2.5-tts` — **default model, best audio quality**
- `step-tts-2` — economy alternative, lower cost
- `step-tts-mini` — also observed in session notes

Model selection should remain a `--model` CLI flag; default is `stepaudio-2.5-tts`.

### Response Behaviour (confirmed)

StepFun returns a **complete audio file per request**, not a streaming
byte stream. The AI SDK's `generateSpeech()` resolves with a full
`result.audio.uint8Array` that is written to disk in a single
`Bun.write()` (or equivalent) call. This means:

- No streaming chunk assembly needed per request
- Each chunk produces one self-contained MP3 file on disk
- The audio assembler simply concatenates those files

### Request Shape

The API follows OpenAI's `audio/speech` convention:

```json
{
  "model": "step-tts-2",
  "input": "text to narrate",
  "voice": "alloy",
  "response_format": "mp3"
}
```

The AI SDK's `generateSpeech` call maps directly to this shape.

### Voice Support

- Default voice: `alloy` (the Hermes `generate-audio` workspace default)
- StepFun may not support every OpenAI voice name. The voice catalogue
  should be validated at startup (probe call or docs lookup) and surfaced
  to the user via `--list-voices`
- Voice cloning is available on StepFun's platform but is **out of scope
  for v1**

### Per-Request Character Limit

- StepFun publishes a hard cap of **1 000 characters** per request for
  TTS calls (documented at the Audio Usage Limits page)
- The chunking engine must split text into chunks of at most 1 000 chars
  each, always on sentence boundaries
- This is a hard API constraint, not a tuning parameter

### Rate Limits

- StepFun does not publish hard TPM (tokens per minute) figures in public docs
- The client must handle `429` / `Retry-After` gracefully with exponential
  backoff
- Track cumulative API usage for cost estimation

### Pricing (as provided)

- `stepaudio-2.5-tts`: $0.85 per 10 000 characters — **default model, better audio quality**
- `step-tts-2`: $0.40 per 10 000 characters — economy alternative
- Voice cloning (separate feature, not used in v1): $1.50 per voice
- Voice cloning + `stepaudio-2.5-tts`/`step-tts-2` combo: $1.50 per voice

### API Key

- Environment variable: `STEPFUN_API_KEY`
- Also accepted: `STEPFUN_STEP_PLAN_API_KEY` (fallback in Hermes plugin)
- Base URL override: `STEPFUN_BASE_URL` (defaults to `https://api.stepfun.ai/v1`)
- The key is managed outside this repository. Do not commit keys or
  examples that look like real keys

---

## Output Design

- **Default**: Single MP3 file per textbook
- **Option**: `--split-by-chapter` produces one MP3 per chapter
- **Metadata**: ID3v2 tags — title, author, album (book title),
  track numbers for chapters
- **Naming convention**:
  - Single file: `<slug>_audiobook.mp3`
  - Chapter files: `<slug>_chapter_001.mp3`, etc.

---

## Key Design Decisions (locked for v1)

- Text-only narration. No SSML, no pronunciation dictionaries, no
  multi-speaker roles. Keep the TTS call a plain text string.
- Synchronous, local pipeline. No job queue or background worker for v1;
  long runs block the terminal session. A progress bar is sufficient.
- No cloud storage. Everything lives on local disk.
- Python as the implementation language. `uv` for package management;
  minimum Python 3.12.

---

## Library Candidates (to validate during implementation)

- PDF extraction: `pymupdf` (fitz)
- EPUB parsing: `ebooklib`
- Audio concatenation: `pydub`
- ID3 tagging: `mutagen`
- CLI framework: `click` or `typer`
- HTTP client: `openai` Python SDK (OpenAI-compatible; pointed at StepFun base URL)

---

## Open Questions to Resolve Before Coding

1. **Per-request character limit**: StepFun publishes a hard cap of
   1 000 characters per request (confirmed from Audio Usage Limits docs).
   The chunking engine must split at ≤1 000 chars on sentence boundaries.
   This is now a known constraint, not an open question — it just needs
   to be wired into the implementation.

---

## References

- OpenAI Python SDK reference: https://developers.openai.com/api/reference/python
- StepFun TTS developer guide: https://platform.stepfun.ai/docs/en/guides/developer/tts
- StepFun `stepaudio-2.5-tts` model page: https://platform.stepfun.ai/docs/en/guides/models/stepaudio-2.5-tts
- StepFun Audio Usage Limits (1 000 char per-call cap): https://platform.stepfun.ai/docs/en/guides/models/audio#usage-limits
