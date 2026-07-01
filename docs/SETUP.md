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
- A **StepFun API key** (only needed for real synthesis, not for `--dry-run` /
  `list-*`)

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

The key is read from the environment — never hard-code or commit it.

```bash
export STEPFUN_API_KEY="sk-..."
```

Add that line to your shell profile (`~/.zshrc`, `~/.bashrc`) to persist it.

Accepted variables:

| Variable | Purpose |
| --- | --- |
| `STEPFUN_API_KEY` | Primary key name (checked first). |
| `STEPFUN_STEP_PLAN_API_KEY` | Fallback key name if the above is unset. |
| `STEPFUN_BASE_URL` | Optional API base URL override (default `https://api.stepfun.ai/v1`). |

> `--dry-run`, `lecturn list-models`, and `lecturn list-voices` work **without**
> a key — handy for verifying the install before adding credentials.

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
