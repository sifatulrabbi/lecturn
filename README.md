# lecturn

Convert textbooks — **PDF, EPUB, plain text, or Markdown** — into narrated MP3
audiobooks. Pick a TTS provider with `--provider`:
[StepFun](https://platform.stepfun.ai) (default), [OpenRouter](https://openrouter.ai)
(Kokoro-82M), or a **self-hosted local Kokoro server** (free/offline — see
[`server/`](server/README.md)).

```
load → clean → chunk → synthesize (StepFun / OpenRouter / local TTS) → assemble (MP3 + ID3)
```

- **Three providers** — StepFun (premium/economy), OpenRouter's Kokoro-82M
  (cheap, open-weight), or a local Kokoro server (`--provider local`: free, runs
  on your own hardware, no per-request cost); pick with `--provider`.
- **Chapter-aware** — detects chapters (PDF TOC, EPUB spine, `#`/`##`, `---`) and
  can emit one tagged MP3 per chapter.
- **Fast, within limits** — bounded concurrency + an RPM throttle; faster at the
  same cost.
- **Resumable** — every chunk is cached with atomic, fingerprinted writes, so an
  interrupted run continues where it left off without re-billing.
- **ID3 tags** — title, author, album, and per-chapter track numbers.

## Quick start

```bash
uv tool install .                          # install the `lecturn` command
export STEPFUN_API_KEY="sk-..."            # your StepFun key (default provider)
# or, for Kokoro: export OPENROUTER_API_KEY="sk-or-..."

lecturn convert mybook.pdf --dry-run                  # free: shows plan + cost estimate
lecturn convert mybook.pdf -o output/                 # convert (prompts to confirm cost)
lecturn convert mybook.pdf --provider openrouter      # narrate with Kokoro-82M
lecturn convert mybook.pdf --provider local           # free: your own Kokoro server
```

For `--provider local`, start the bundled server first (`cd server && uv sync &&
uv run lecturn-kokoro`) or point `--base-url` at any OpenAI-compatible Kokoro
server. See [`server/README.md`](server/README.md).

`--dry-run`, `lecturn list-models`, and `lecturn list-voices` need no API key.
Requires **ffmpeg** on your `PATH`.

## Documentation

| Guide | What's in it |
| --- | --- |
| **[docs/SETUP.md](docs/SETUP.md)** | Requirements, install (global or from source), credentials, verifying the install. |
| **[docs/USAGE.md](docs/USAGE.md)** | Every command and option, extensive example combinations, recipes, speed/cost, resuming, voices, models, troubleshooting, scripting. |
| **[docs/DEV.md](docs/DEV.md)** | Repo layout, pipeline internals, testing, design decisions & invariants, contributing. |
| **[PLAN.md](PLAN.md)** | The original design spec. |

AI agents working in this repo: see [CLAUDE.md](CLAUDE.md).

## Scope (v1)

Text-only narration. No SSML, no streaming playback, no multi-voice casting, no
translation, no GUI, no cloud storage.

## License

MIT — see [`pyproject.toml`](pyproject.toml).
