# lecturn

Convert textbooks — **PDF, EPUB, plain text, or Markdown** — into narrated MP3
audiobooks using a pluggable TTS **provider**:
[StepFun](https://platform.stepfun.ai) or [OpenRouter](https://openrouter.ai).

```
load → clean → chunk → synthesize (StepFun | OpenRouter TTS) → assemble (MP3 + ID3)
```

- **Multi-provider** — pick a backend with `--provider`; each brings its own
  models, voices, pricing, and limits. Adding another OpenAI-compatible provider
  is a small, self-contained module.
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
export STEPFUN_API_KEY="sk-..."            # your provider key (or OPENROUTER_API_KEY)

lecturn list-providers                                   # see the available providers
lecturn convert mybook.pdf -p stepfun --dry-run          # free: shows plan + cost estimate
lecturn convert mybook.pdf -p stepfun -o output/         # convert (prompts to confirm cost)
```

`--provider` is required. `--dry-run`, `lecturn list-providers`,
`lecturn list-models`, and `lecturn list-voices` need no API key. Requires
**ffmpeg** on your `PATH`.

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
