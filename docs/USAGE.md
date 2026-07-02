# lecturn — Usage Guide

`lecturn` converts a textbook (**PDF, EPUB, plain text, or Markdown**) into a
narrated MP3 audiobook using a TTS **provider** (StepFun or OpenRouter). This
guide covers every command and option with extensive, copy-pasteable examples.

> New here? See [SETUP.md](SETUP.md) to install the `lecturn` command and set
> your API key first.

Throughout, examples assume the global command `lecturn`. If you're running from
a source checkout without installing, prefix everything with `uv run` — e.g.
`uv run lecturn convert book.pdf --provider stepfun`.

> **`--provider` is required.** Every command that talks to a service — `convert`,
> `list-models`, `list-voices` — needs `--provider <name>` (`-p` for short).
> Examples below use `stepfun`; swap in `openrouter` as needed. See
> [Providers](#providers). `list-providers` and `--dry-run` still need no key.

---

## Table of contents

- [Commands at a glance](#commands-at-a-glance)
- [Providers](#providers)
- [Quick start](#quick-start)
- [The `convert` command](#the-convert-command)
- [Input formats](#input-formats)
- [Examples by scenario](#examples-by-scenario)
- [Recipes (ready-made presets)](#recipes-ready-made-presets)
- [Speed, cost & rate limits](#speed-cost--rate-limits)
- [Resuming & caching](#resuming--caching)
- [Choosing a voice](#choosing-a-voice)
- [Choosing a model](#choosing-a-model)
- [Output files](#output-files)
- [Environment variables](#environment-variables)
- [Exit codes](#exit-codes)
- [Troubleshooting](#troubleshooting)
- [Scripting & automation](#scripting--automation)

---

## Commands at a glance

```bash
lecturn convert INPUT_FILE -p PROVIDER [OPTIONS]   # convert a book into audio (main command)
lecturn list-providers                             # show the available providers (no key)
lecturn list-models -p PROVIDER                    # show a provider's models and pricing
lecturn list-voices -p PROVIDER                    # show a provider's voice catalogue
lecturn --version                                  # print version
lecturn --help                                     # top-level help
lecturn convert --help                             # full option reference for convert
```

---

## Providers

A **provider** is the TTS service that turns text into audio. Pick one per run
with `--provider` / `-p`. There is no default.

| Provider | `--provider` | Default model | Voices | API key env |
| --- | --- | --- | --- | --- |
| **StepFun** | `stepfun` | `stepaudio-2.5-tts` | shared across models | `STEPFUN_API_KEY` |
| **OpenRouter** | `openrouter` | `openai/gpt-4o-mini-tts` | **model-specific** | `OPENROUTER_API_KEY` |

```bash
lecturn list-providers                         # names, base URLs, default models, key env
lecturn list-models  --provider openrouter     # a provider's models + (approx.) pricing
lecturn list-voices  --provider openrouter     # a provider's voices
```

Key differences that affect how you use them:

- **Char limit.** StepFun caps a request at **1000** characters; OpenRouter at
  **2000**. `--max-chars` defaults to (and can't exceed) the provider's cap.
- **Voices.** StepFun voices work across its models. **OpenRouter voices are
  model-specific** — `list-voices --provider openrouter` shows the *default*
  model's voices; other models list theirs on their page at
  `https://openrouter.ai/<model-id>`.
- **Automatic fallback.** On StepFun it defaults to the economy model
  (`step-tts-2`). On OpenRouter it's **off** by default (swapping models would
  break the model-specific voice); opt in with `--fallback-model` if you know a
  compatible model.
- **Pricing.** OpenRouter bills per token, so its cost estimates are approximate.

---

## Quick start

```bash
# 1. See the plan and cost WITHOUT calling the API (free, no key needed):
lecturn convert mybook.pdf --provider stepfun --dry-run

# 2. Convert for real (prompts once to confirm the estimated cost):
lecturn convert mybook.pdf --provider stepfun -o output/

# 3. Browse providers, then a provider's voices and models:
lecturn list-providers
lecturn list-voices --provider stepfun
lecturn list-models --provider stepfun
```

---

## The `convert` command

```
lecturn convert INPUT_FILE [OPTIONS]
```

`INPUT_FILE` is required. Supported extensions: `.pdf`, `.epub`, `.md`,
`.markdown`, `.txt`, `.text`.

| Option | Default | Description |
| --- | --- | --- |
| `-p`, `--provider` | **required** | TTS provider: `stepfun` or `openrouter`. See [Providers](#providers). |
| `-o`, `--output` | `output/` | Directory for the output MP3(s). Created if missing. |
| `-m`, `--model` | *provider default* | TTS model. Defaults to the provider's best-quality model. See [`list-models`](#choosing-a-model). |
| `--voice` | *provider default* | Voice ID. Defaults to the provider's default voice. See [`list-voices`](#choosing-a-voice). |
| `--fallback-model` | *provider default* | Model to retry with if the primary is rejected (quota/entitlement/unknown-model). StepFun: economy model; OpenRouter: none. `none` disables it. |
| `--title` | *(from file/metadata)* | Override the book title (used in ID3 tags and output filename). |
| `--author` | *(from metadata or "Unknown")* | Override the author (used in the artist tag). |
| `--split-by-chapter` | off | Emit one MP3 per chapter (with track numbers) instead of a single file. |
| `-c`, `--concurrency` | `3` | Chunks synthesized in parallel. **Faster at the same cost.** Keep ≤ your provider's per-model limit; `1` = strictly sequential. |
| `--rpm` | *provider default* | Throttle: max requests started per minute. Defaults to the provider's per-model RPM guidance. `0` disables the throttle. |
| `--max-chars` | *provider cap* | Max characters per chunk. Defaults to (and cannot exceed) the provider's hard cap (StepFun 1000, OpenRouter 2000). |
| `--base-url` | *provider default* | Override the provider's API base URL. |
| `--no-resume` | off | Ignore all cached audio and re-synthesize every chunk from scratch. |
| `--dry-run` | off | Load + clean + chunk only; print stats and cost estimate. **No API calls.** |
| `-y`, `--yes` | off | Skip the cost-estimate confirmation prompt. |

---

## Input formats

How chapter boundaries are detected per format (this drives `--split-by-chapter`
and the per-chapter tags):

| Format | Chapters come from | Notes |
| --- | --- | --- |
| **PDF** (`.pdf`) | Embedded table of contents (bookmarks/outline) | No TOC → one chapter for the whole book. Image-only PDFs (no text layer) are rejected — OCR is out of scope. |
| **EPUB** (`.epub`) | Each spine document (usually one per chapter) | Chapter title taken from the first heading. Title/author read from EPUB metadata. |
| **Markdown** (`.md`, `.markdown`) | `#` and `##` headings | Deeper headings stay inline. The first `#` becomes the book title. |
| **Plain text** (`.txt`, `.text`) | A line containing only `---` (3+ dashes) | No delimiter → one chapter. Title = first non-blank line. |

```bash
lecturn convert thesis.pdf --dry-run          # PDF: chapters from the TOC
lecturn convert novel.epub --dry-run          # EPUB: chapters from the spine
lecturn convert notes.md --dry-run            # Markdown: chapters from #/##
lecturn convert transcript.txt --dry-run      # TXT: chapters split on ---
```

---

## Examples by scenario

### Estimate first — always free

`--dry-run` loads, cleans, and chunks the book, then prints chapters, chunk
count, characters, and an estimated cost. It makes **no API calls** and needs no
API key:

```bash
lecturn convert mybook.pdf --dry-run
lecturn convert mybook.pdf --dry-run --model step-tts-2      # cost at economy price
lecturn convert mybook.pdf --dry-run --max-chars 800         # see how chunk count changes
```

### Simplest real conversion

```bash
lecturn convert mybook.pdf
# -> output/<title>_audiobook.mp3   (prompts to confirm cost first)
```

### Pick an output directory

```bash
lecturn convert mybook.epub -o ~/audiobooks/
lecturn convert mybook.epub --output ./out/clear-thinking/
```

### Choose the model (quality vs. cost)

```bash
lecturn convert mybook.pdf --model stepaudio-2.5-tts    # best quality (default)
lecturn convert mybook.pdf --model step-tts-2           # ~2x cheaper economy model
lecturn convert mybook.pdf -m step-tts-2                # short form
```

### Choose a voice

```bash
lecturn list-voices                                     # browse the catalogue
lecturn convert mybook.pdf --voice lively-girl          # default
lecturn convert mybook.pdf --voice boyinnansheng        # "Broadcast Male"
lecturn convert mybook.pdf --voice elegantgentle-female
```

### One file vs. one file per chapter

```bash
lecturn convert mybook.epub                             # single combined MP3
lecturn convert mybook.epub --split-by-chapter          # one tagged MP3 per chapter
```

Per-chapter output gets `TRCK` track numbers (`1/12`, `2/12`, …), so players show
them in order.

### Set the title and author (metadata + filename)

```bash
lecturn convert raw.txt --title "Meditations" --author "Marcus Aurelius"
lecturn convert scan.pdf --title "Domain-Driven Design" --author "Eric Evans" \
  --split-by-chapter
```

### Go faster (same cost)

```bash
lecturn convert mybook.pdf --concurrency 1              # strictly sequential (slowest, safest)
lecturn convert mybook.pdf --concurrency 3              # default
lecturn convert mybook.pdf --concurrency 5 --rpm 10     # max within StepFun limits (~5x faster)
lecturn convert mybook.pdf -c 5                         # short form
```

See [Speed, cost & rate limits](#speed-cost--rate-limits) for the math.

### Skip the confirmation prompt (automation)

```bash
lecturn convert mybook.pdf -y
lecturn convert mybook.pdf --yes --model step-tts-2 --concurrency 5
```

### Tune chunk size

```bash
lecturn convert mybook.pdf --max-chars 1000            # default (StepFun hard cap)
lecturn convert mybook.pdf --max-chars 600             # smaller chunks (more requests)
```

Smaller chunks = more requests = finer resume granularity, but no cost change
(cost is per character). You **cannot** exceed 1000 — the API rejects it.

### Start fresh (ignore the cache)

```bash
lecturn convert mybook.pdf --no-resume                 # ignore cache, redo every chunk
```

### Point at a different endpoint

```bash
lecturn convert mybook.pdf --base-url https://my-proxy.example.com/v1
# or via env:
STEPFUN_BASE_URL=https://my-proxy.example.com/v1 lecturn convert mybook.pdf
```

### The "kitchen sink" — a realistic full run

```bash
lecturn convert ./domain-driven-design.pdf \
  --output ~/audiobooks/ddd/ \
  --model step-tts-2 \
  --voice boyinnansheng \
  --split-by-chapter \
  --title "Domain-Driven Design" \
  --author "Eric Evans" \
  --concurrency 5 --rpm 10 \
  --yes
```

---

## Recipes (ready-made presets)

**Just tell me the cost** (free, no key):
```bash
lecturn convert book.pdf --dry-run
```

**Cheapest full book** (economy model, max safe speed):
```bash
lecturn convert book.pdf --model step-tts-2 --concurrency 5 --rpm 10 -y
```

**Best quality, no rush** (premium model, gentle sequential pace):
```bash
lecturn convert book.pdf --model stepaudio-2.5-tts --concurrency 1
```

**Fastest within the plan limits**:
```bash
lecturn convert book.pdf --concurrency 5 --rpm 10
```

**Podcast-style, per chapter, tagged**:
```bash
lecturn convert book.epub --split-by-chapter \
  --title "My Book" --author "Author Name" --voice lively-girl
```

---

## Speed, cost & rate limits

`--concurrency` changes **wall-clock time only, not cost.** The price is a
function of total characters synthesized (`characters / 10,000 × model price`),
identical whether you run serially or in parallel.

The ceiling is your StepFun plan's **per-model** limits. For a standard account:

| Limit | Value |
| --- | --- |
| Concurrency (in-flight requests) | 5 |
| RPM (requests/minute) | 10 |
| TPM (tokens/minute) | 5,000,000 (effectively unlimited here) |

At roughly ~30s per ~900-character request, **5 in flight ≈ 10 completions/min**,
which is exactly the RPM cap — so `--concurrency 5 --rpm 10` pins you at the
maximum safe rate (about **5× faster** than sequential). Going above 5 just
queues behind the throttle and risks `429` rate-limit errors.

Rough guide for an 800k-character book (~890 chunks):

| `--concurrency` | Approx. wall-clock | Cost (step-tts-2) |
| --- | --- | --- |
| 1 | ~7.5 h | ~$32 |
| 3 | ~2.5 h | ~$32 |
| 5 | ~1.5 h | ~$32 |

> The `--rpm` throttle counts request **starts** in a rolling 60-second window.
> Set it to your account's RPM limit; `--rpm 0` disables throttling (rely on the
> client's automatic `429` backoff instead).

---

## Resuming & caching

Runs are resumable by design:

- Every chunk is cached to `OUTPUT/.audiobook_cache/<book-slug>/` the moment it's
  synthesized.
- Writes are **atomic** (temp file + rename), so an interrupt — Ctrl-C, crash,
  power loss — can never leave a half-written file.
- Cache filenames embed a **fingerprint** of the **provider + voice + response
  format + chunk text** — deliberately **not** the model. So:
  - **Re-running the same command resumes** — already-done chunks are skipped and
    not re-billed.
  - **Switching `--model` reuses the cache.** A book can legitimately span models
    (e.g. the premium→economy fallback on a quota outage), so the model isn't
    part of the key. Use `--no-resume` if you want to regenerate everything.
  - **Changing the `--provider` or `--voice`, or the source text (including via
    `--max-chars`), invalidates stale audio** — those chunks are re-synthesized
    instead of silently reused. (Provider is in the key because a voice ID like
    `alloy` could exist under more than one provider yet sound different.)
- Cache files are validated as real MP3s on reuse; corrupt/empty ones are
  re-synthesized rather than trusted.

```bash
# Start a long run:
lecturn convert bigbook.pdf --concurrency 5 --rpm 10

# ... press Ctrl-C anytime. The tool prints a resume hint and exits cleanly.

# Resume — just run the exact same command again; done chunks are skipped:
lecturn convert bigbook.pdf --concurrency 5 --rpm 10

# Start completely fresh (ignore the cache):
lecturn convert bigbook.pdf --no-resume

# Clear the cache manually to reclaim disk:
rm -rf output/.audiobook_cache
```

> Because the cache is keyed by content, switching `--voice` or `--model`
> mid-project leaves the old chunks on disk (unused). Delete
> `output/.audiobook_cache` if you want to reclaim that space.

---

## Choosing a voice

Voices are provider-specific. Browse a provider's set with
`lecturn list-voices --provider <name>`. On **OpenRouter** voices are also
*model*-specific — the listing shows the default model's voices; other models
list theirs at `https://openrouter.ai/<model-id>`.

The rest of this section covers **StepFun**, which uses its own voice IDs
(**not** OpenAI names like `alloy`):

```bash
lecturn list-voices --provider stepfun
```

The default is **`lively-girl`**. A few commonly useful, widely-available voices:

| Voice ID | Character |
| --- | --- |
| `lively-girl` | Lively girl (English-keyed, default) |
| `elegantgentle-female` | Elegant, gentle female |
| `vibrant-youth` | Vibrant youth |
| `soft-spoken-gentleman` | Soft-spoken gentleman |
| `magnetic-voiced-male` | Magnetic male |
| `boyinnansheng` | Broadcast male |
| `wenrounansheng` | Gentle male |
| `zhixingjiejie` | Intellectual lady |

> **Voice access is per-account.** A voice can be a valid catalogue entry yet
> return `voice_id_invalid` ("you do not have access to it") on your account.
> The English-keyed voices tend to be the most broadly available — if a
> Pinyin-keyed voice is rejected, try one of those. Run `lecturn list-voices` for
> the full set (~36 voices).

---

## Choosing a model

```bash
lecturn list-models --provider stepfun
lecturn list-models --provider openrouter
```

**StepFun:**

| Model | Price / 10k chars | Notes |
| --- | --- | --- |
| `stepaudio-2.5-tts` | $0.85 | Default. Best audio quality. |
| `step-tts-2` | $0.40 | Economy. ~2× cheaper. |

**OpenRouter** (curated set; prices are *approximate* per-character estimates —
OpenRouter bills per token — verify on each model's page):

| Model | ≈ Price / 10k chars | Notes |
| --- | --- | --- |
| `openai/gpt-4o-mini-tts` | ~$0.006 | Default. OpenAI voices (alloy, nova, …). |
| `google/gemini-3.1-flash-tts-preview` | ~$0.010 | Google Gemini voices (preview). |
| `mistralai/voxtral-mini-tts-2603` | ~$0.016 | Mistral Voxtral. |
| `hexgrad/kokoro-82m` | ~$0.001 | Cheapest; open-weights; 54 voices. |

**Automatic fallback:** if the primary `--model` is rejected for a
quota/entitlement/unknown-model reason, `lecturn` retries with `--fallback-model`
and continues on that model. On **StepFun** this defaults to `step-tts-2`. On
**OpenRouter** it is **off by default** — voices are model-specific, so a silent
model swap would break the voice; pass `--fallback-model` only with a
voice-compatible model. When a fallback happens you'll see a `Note: fell back
from '…' to '…'` message, and the cost estimate reflects the model actually used.

> **Tip:** if your account is out of quota on `stepaudio-2.5-tts`, pass
> `--model step-tts-2` directly to skip the wasted first attempt on each run.
> Auth (`401`) and bad-voice errors never trigger a fallback (a model swap can't
> fix them).

---

## Output files

Files are named from a slug of the book title:

| Mode | Output |
| --- | --- |
| Single file (default) | `OUTPUT/<slug>_audiobook.mp3` |
| `--split-by-chapter` | `OUTPUT/<slug>_chapter_001.mp3`, `…_chapter_002.mp3`, … |

All outputs get ID3v2 tags: title (`TIT2`), author/artist (`TPE1`), album
(`TALB`), and — for per-chapter output — track numbers (`TRCK`, e.g. `3/12`).

---

## Environment variables

Each provider reads its own key/base-URL variables:

| Variable | Purpose |
| --- | --- |
| `STEPFUN_API_KEY` | StepFun key (required for real StepFun synthesis). |
| `STEPFUN_STEP_PLAN_API_KEY` | Alternative StepFun key name, used if `STEPFUN_API_KEY` is unset. |
| `STEPFUN_BASE_URL` | Override the StepFun base URL (or use `--base-url`). |
| `OPENROUTER_API_KEY` | OpenRouter key (required for real OpenRouter synthesis). |
| `OPENROUTER_BASE_URL` | Override the OpenRouter base URL (or use `--base-url`). |

```bash
export STEPFUN_API_KEY="sk-..."
export OPENROUTER_API_KEY="sk-or-..."
```

`--dry-run`, `list-providers`, `list-models`, and `list-voices` need **no** key.

---

## Exit codes

Useful for scripting:

| Code | Meaning |
| --- | --- |
| `0` | Success (including `--dry-run`, `--version`, `--help`). |
| `1` | Load/config/pipeline error (bad input file, missing API key, synthesis failed). |
| `2` | Invalid arguments (missing/unknown `--provider`, `--max-chars` out of range, `--concurrency < 1`, `--rpm < 0`). |
| `130` | Interrupted with Ctrl-C (chunks done so far are cached; re-run to resume). |

---

## Troubleshooting

| Symptom | Cause & fix |
| --- | --- |
| `StepFun rejected the voice … voice_id_invalid` | The voice isn't enabled for your account. Run `list-voices` and try another — English-keyed voices are the most available. |
| `quota_exceeded` / HTTP 402 | Out of credit for that **model**. It's per-model: `step-tts-2` may work when `stepaudio-2.5-tts` doesn't. Top up, or pass `--model step-tts-2`. |
| `authentication failed` / HTTP 401 | Bad/missing key. Check `STEPFUN_API_KEY`. (No fallback — a model swap can't fix a bad key.) |
| `Unsupported file type` | Use `.pdf`, `.epub`, `.md`, `.markdown`, `.txt`, or `.text`. |
| `image-only PDF with no text layer` | The PDF has no extractable text (scanned images). OCR is out of scope for v1. |
| `Failed to decode … Is ffmpeg installed?` | Install ffmpeg and ensure it's on `PATH` (`brew install ffmpeg`). |
| Frequent `429` / slow with backoff | You're above your rate limit. Lower `--concurrency` and/or set `--rpm` to your plan's limit. |
| Assembly complains about a chunk | A cache file is corrupt; it's normally re-synthesized automatically. If needed, `rm -rf output/.audiobook_cache` and re-run. |

---

## Scripting & automation

**Batch-convert a folder of books** (economy model, unattended):

```bash
for book in ~/books/*.epub; do
  lecturn convert "$book" -o ~/audiobooks/ \
    --model step-tts-2 --concurrency 5 --rpm 10 --split-by-chapter -y
done
```

**Estimate cost across many files without spending anything:**

```bash
for book in ~/books/*.pdf; do
  echo "== $book =="
  lecturn convert "$book" --dry-run
done
```

**Fail fast in CI** (non-zero exit stops the script):

```bash
set -euo pipefail
lecturn convert manuscript.md -o dist/ --model step-tts-2 -y
```

**Resume-friendly long run** (safe to re-invoke on the same output dir):

```bash
lecturn convert bigbook.pdf -o out/ --concurrency 5 --rpm 10 -y || \
  echo "Interrupted — re-run the same command to resume."
```
