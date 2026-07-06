# lecturn — Setup Guide

How to install the `lecturn` command and configure credentials. For day-to-day
usage after this, see [USAGE.md](USAGE.md).

---

## Requirements

- **Python 3.12+**
- **[`uv`](https://docs.astral.sh/uv/)** for packaging/dependency management
- **ffmpeg** on your `PATH` — used by `pydub` to concatenate MP3 chunks
  - macOS: `brew install ffmpeg`
  - Debian/Ubuntu: `sudo apt-get install ffmpeg`
  - Windows: `winget install Gyan.FFmpeg` (or `choco install ffmpeg`)
- A **TTS provider API key** — a **StepFun** key (default provider) and/or an
  **OpenRouter** key (for `--provider openrouter`, Kokoro-82M). Only needed for
  real synthesis, not for `--dry-run` / `list-*`. The third provider,
  `--provider local` (a self-hosted Kokoro server), needs **no key at all**.

Check ffmpeg is available:

```bash
ffmpeg -version
```

---

## Install

### Option A — global command (recommended)

Installs an isolated tool environment and puts the `lecturn` command on your
`PATH`:

```bash
uv tool install .
```

Then, from any directory:

```bash
lecturn --help
lecturn --version
```

If the shell can't find `lecturn` afterward, add uv's tool bin directory to your
`PATH`:

```bash
uv tool update-shell     # then open a new terminal
```

### Option B — from a source checkout (development)

No global install; run through `uv`:

```bash
uv sync                     # create .venv with pinned dependencies
uv run lecturn --help       # run without installing
```

For a dev setup that includes test dependencies, see [DEV.md](DEV.md).

---

## Configure credentials

Keys are read from the environment — never hard-code or commit them. You need the
key for the provider you use (`--provider`, default `stepfun`). A StepFun run can
also fall back to OpenRouter/Kokoro on a quota outage, so set `OPENROUTER_API_KEY`
too if you want that safety net (it's read only when the fallback fires; opt out
with `--fallback-model none`).

### StepFun (default provider)

```bash
export STEPFUN_API_KEY="sk-..."
```

Get a key from [platform.stepfun.ai](https://platform.stepfun.ai). Add the line
to your shell profile (`~/.zshrc`, `~/.bashrc`) to persist it.

| Variable | Purpose |
| --- | --- |
| `STEPFUN_API_KEY` | Primary key name (checked first). |
| `STEPFUN_STEP_PLAN_API_KEY` | Fallback key name if the above is unset. |
| `STEPFUN_BASE_URL` | Optional API base URL override (default `https://api.stepfun.ai/v1`). |

### OpenRouter (`--provider openrouter`, Kokoro-82M)

```bash
export OPENROUTER_API_KEY="sk-or-..."
```

Create a key at
[openrouter.ai/settings/keys](https://openrouter.ai/settings/keys). Kokoro-82M
is a cheap open-weight voice model (~$0.62 / 1M chars).

| Variable | Purpose |
| --- | --- |
| `OPENROUTER_API_KEY` | Your OpenRouter API key. Also used for the automatic StepFun→Kokoro fallback (only when it fires; `--fallback-model none` opts out). |
| `OPENROUTER_BASE_URL` | Optional API base URL override (default `https://openrouter.ai/api/v1`). |

### Local Kokoro server (`--provider local`)

Run Kokoro-82M on your own hardware and point `lecturn` at it — **no API key
required**, and every request is free (you pay only for your own compute). It
works with any OpenAI-compatible Kokoro server: the bundled one (see
[`server/README.md`](../server/README.md) — added by the local-server slice, so
it may not exist in your checkout yet) or a community server such as
[Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI).

```bash
# Start your Kokoro server on port 8880, then:
lecturn convert mybook.pdf --provider local
```

Both environment variables are **optional**:

| Variable | Purpose |
| --- | --- |
| `LOCAL_TTS_BASE_URL` | Override the server URL (default `http://127.0.0.1:8880/v1`; also settable with `--base-url`). |
| `LOCAL_TTS_API_KEY` | Only if you front the server with an authenticating proxy. Unset ⇒ a placeholder key is used (the OpenAI SDK requires a non-empty string). |

> The automatic OpenRouter/Kokoro fallback is **disabled by default** for a
> local primary, so a stopped local server never silently spends OpenRouter
> money. Pass `--fallback-model hexgrad/kokoro-82m` (and set `OPENROUTER_API_KEY`)
> to opt in.

> `--dry-run`, `lecturn list-models`, and `lecturn list-voices` work **without**
> any key — handy for verifying the install before adding credentials.

A [`.env.example`](../.env.example) at the repo root lists every accepted
variable.

---

## Verify the install

```bash
lecturn --version                 # prints: lecturn <version>
lecturn list-models               # no API call
lecturn list-voices               # no API call

# End-to-end plan without spending anything (needs no key):
echo "# Test\n\nHello world. This is a test." > /tmp/test.md
lecturn convert /tmp/test.md --dry-run
```

A successful `--dry-run` prints a conversion plan (chapters, chunks, characters,
estimated cost) — confirming loaders, cleaner, and chunker all work.

---

## Update

Snapshot installs don't track source changes automatically — reinstall after
pulling updates or editing the code:

```bash
git pull
uv tool install . --reinstall
```

---

## Uninstall

The command is `lecturn`, but the distribution (package) name is
`textbook-audiobook`:

```bash
uv tool uninstall textbook-audiobook
```

---

## Notes

- The command is named `lecturn`; the underlying Python package is
  `textbook_audiobook`. You can also run it as a module from a source checkout:
  `uv run python -m textbook_audiobook ...`.
- `output/`, `*.mp3`, the `.audiobook_cache/`, and `.env` are git-ignored, so
  generated audio and secrets won't be committed.
