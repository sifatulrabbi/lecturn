"""Command-line interface (Typer).

Subcommands:
  convert       Convert an input file into an audiobook (the main command).
  list-voices   Show the known voice catalogues (per provider).
  list-models   Show available models and pricing (per provider).

Three TTS providers are selectable via ``--provider``: ``stepfun`` (the default,
premium/economy StepFun models), ``openrouter`` (Kokoro-82M), and ``local`` (a
self-hosted, OpenAI-compatible Kokoro server). ``--model`` / ``--voice`` /
``--fallback-model`` default per provider — pass them to override.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from textbook_audiobook import __version__
from textbook_audiobook.config import (
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    HARD_CHAR_LIMIT,
    KOKORO_VOICES,
    LOCAL_DEFAULT_MODEL,
    LOCAL_DEFAULT_VOICE,
    LOCAL_MODELS,
    LOCAL_VOICES,
    MODELS,
    OPENROUTER_DEFAULT_MODEL,
    OPENROUTER_DEFAULT_VOICE,
    OPENROUTER_MODELS,
    VOICES,
    LocalConfig,
    MissingApiKeyError,
    OpenRouterConfig,
    StepFunConfig,
    TTSConfig,
)
from textbook_audiobook.loaders import LoaderError, SUPPORTED_EXTENSIONS
from textbook_audiobook import pipeline
from textbook_audiobook.pipeline import DEFAULT_RPM, MAX_USEFUL_CONCURRENCY
from textbook_audiobook.tts import LocalTTSClient, OpenRouterTTSClient


class Provider(str, Enum):
    """TTS provider selector.

    ``stepfun`` is the original (and default) provider; ``openrouter`` narrates
    with Kokoro-82M; ``local`` targets a self-hosted, OpenAI-compatible Kokoro
    server. A ``str`` Enum so Typer renders ``[stepfun|openrouter|local]``
    choices and validates them for us.
    """

    stepfun = "stepfun"
    openrouter = "openrouter"
    local = "local"


app = typer.Typer(
    add_completion=False,
    help=(
        "Convert textbooks (PDF/EPUB/TXT/MD) into narrated audiobooks via "
        "StepFun, OpenRouter (Kokoro), or a local Kokoro server."
    ),
    no_args_is_help=True,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        # highlight=False so Rich doesn't inject ANSI colour into the version
        # number — keeps `lecturn --version` clean for piping/parsing.
        console.print(f"lecturn {__version__}", highlight=False)
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Textbook → Audiobook."""


def _resolve_fallback(
    fallback_model: str | None,
    primary_model: str,
    *,
    default: str | None = OPENROUTER_DEFAULT_MODEL,
) -> str | None:
    """Resolve ``--fallback-model`` to an OpenRouter model id, or ``None``.

    A configured fallback ALWAYS routes to OpenRouter/Kokoro regardless of the
    primary provider. Rules:

    - omitted (``None``) → ``default`` (OpenRouter's default model for the
      StepFun/OpenRouter primaries; ``None`` for a ``local`` primary, so a dead
      local server never silently spends OpenRouter money);
    - ``"none"`` or empty → ``None`` (fallback disabled);
    - equal to the primary model → ``None`` (a no-op; e.g. OpenRouter primary
      with the default Kokoro fallback);
    - otherwise the given value (interpreted as an OpenRouter model). An explicit
      value re-enables the OpenRouter fallback even for a ``local`` primary.
    """

    if fallback_model is None:
        resolved: str | None = default
    elif fallback_model.strip().lower() in {"none", ""}:
        return None
    else:
        resolved = fallback_model.strip()
    if resolved == primary_model:
        return None
    return resolved


@app.command()
def convert(
    input_file: Path = typer.Argument(
        ...,
        exists=False,
        help=f"Source file. Supported: {', '.join(SUPPORTED_EXTENSIONS)}.",
    ),
    output_dir: Path = typer.Option(
        Path("output"), "--output", "-o", help="Directory for output MP3(s)."
    ),
    provider: Provider = typer.Option(
        Provider.stepfun, "--provider",
        case_sensitive=False,
        help=(
            "TTS provider: 'stepfun' (default), 'openrouter' (Kokoro-82M), or "
            "'local' (a self-hosted Kokoro server)."
        ),
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m",
        help=(
            "TTS model. Defaults to the provider's best-quality model (stepfun: "
            "stepaudio-2.5-tts, openrouter: hexgrad/kokoro-82m, local: kokoro)."
        ),
    ),
    voice: Optional[str] = typer.Option(
        None, "--voice",
        help=(
            "Voice ID. Defaults per provider (stepfun: lively-girl, openrouter "
            "& local: af_heart)."
        ),
    ),
    fallback_model: Optional[str] = typer.Option(
        None, "--fallback-model",
        help=(
            "Model to fall back to (always via OpenRouter/Kokoro) if the primary "
            "provider rejects a request for quota/entitlement/unknown-model "
            "reasons. Defaults to hexgrad/kokoro-82m for the stepfun/openrouter "
            "primaries and needs OPENROUTER_API_KEY at fallback time. For a "
            "'local' primary it defaults to disabled (so a dead local server "
            "never silently spends OpenRouter money) — pass a model to enable it. "
            "Pass 'none' to disable."
        ),
    ),
    title: Optional[str] = typer.Option(
        None, "--title", help="Override the book title (used in tags/filename)."
    ),
    author: Optional[str] = typer.Option(
        None, "--author", help="Override the author (used in artist tag)."
    ),
    split_by_chapter: bool = typer.Option(
        False, "--split-by-chapter",
        help="Emit one MP3 per chapter instead of a single file.",
    ),
    max_chars: int = typer.Option(
        HARD_CHAR_LIMIT, "--max-chars",
        help=f"Max chars per chunk (hard cap {HARD_CHAR_LIMIT}).",
    ),
    base_url: Optional[str] = typer.Option(
        None, "--base-url",
        help=(
            "Override the provider's API base URL (e.g. point 'local' at a "
            "non-default host/port; default http://127.0.0.1:8880/v1)."
        ),
    ),
    no_resume: bool = typer.Option(
        False, "--no-resume",
        help=(
            "Ignore any cached audio and re-synthesize every chunk from scratch. "
            "By default a run resumes from cache; switching --model reuses the "
            "cache (only a voice or text change invalidates it)."
        ),
    ),
    keep_cache: bool = typer.Option(
        False, "--keep-cache",
        help=(
            "Keep the resume cache after a fully successful run. By default the "
            "cache is deleted once every chunk is synthesized AND all output "
            "file(s) are written — it only exists to resume interrupted runs — so "
            "re-converting the same book afterwards starts from scratch. Pass this "
            "to preserve it (e.g. if you expect to re-run with tweaks). An "
            "interrupted or failed run always keeps the cache regardless."
        ),
    ),
    concurrency: int = typer.Option(
        3, "--concurrency", "-c",
        help=(
            "Number of chunks to synthesize in parallel. Speeds up the run at "
            "the same cost. Keep at/under your account's per-model concurrency "
            f"limit (StepFun: {MAX_USEFUL_CONCURRENCY}). Use 1 for strictly "
            "sequential."
        ),
    ),
    rpm: int = typer.Option(
        DEFAULT_RPM, "--rpm",
        help=(
            "Max requests started per minute (throttle). Set to your account's "
            f"per-model RPM limit (StepFun: {DEFAULT_RPM}). 0 disables the "
            "throttle."
        ),
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Load, clean, and chunk only; print stats and cost estimate. No API calls.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the cost-estimate confirmation prompt."
    ),
) -> None:
    """Convert INPUT_FILE into an audiobook."""

    if max_chars > HARD_CHAR_LIMIT:
        # The 1000-char cap is StepFun's constraint, applied to both providers so
        # the chunking (and resume cache) stays portable between them.
        console.print(
            f"[red]--max-chars {max_chars} exceeds the hard limit of "
            f"{HARD_CHAR_LIMIT} chars (StepFun's cap, applied to both providers "
            "for cache portability).[/red]"
        )
        raise typer.Exit(code=2)
    if max_chars <= 0:
        console.print("[red]--max-chars must be positive.[/red]")
        raise typer.Exit(code=2)

    if concurrency < 1:
        console.print("[red]--concurrency must be at least 1.[/red]")
        raise typer.Exit(code=2)
    if concurrency > MAX_USEFUL_CONCURRENCY:
        # Cite StepFun's limit only for a StepFun run; OpenRouter's limits differ,
        # so stay provider-neutral there.
        limit_note = (
            f"StepFun's per-model concurrency limit ({MAX_USEFUL_CONCURRENCY})"
            if provider is Provider.stepfun
            else f"the default cap ({MAX_USEFUL_CONCURRENCY})"
        )
        console.print(
            f"[yellow]Warning:[/yellow] --concurrency {concurrency} exceeds "
            f"{limit_note}; extra requests will just queue behind the --rpm "
            "throttle."
        )
    if rpm < 0:
        console.print("[red]--rpm cannot be negative (use 0 to disable).[/red]")
        raise typer.Exit(code=2)

    # --- Resolve per-provider defaults --------------------------------------
    # Each flag defaults to None so we can tell "not given" from an explicit
    # value and fill in the right provider default here.
    if provider is Provider.openrouter:
        resolved_model = model if model is not None else OPENROUTER_DEFAULT_MODEL
        resolved_voice = voice if voice is not None else OPENROUTER_DEFAULT_VOICE
        known_models = OPENROUTER_MODELS
    elif provider is Provider.local:
        resolved_model = model if model is not None else LOCAL_DEFAULT_MODEL
        resolved_voice = voice if voice is not None else LOCAL_DEFAULT_VOICE
        known_models = LOCAL_MODELS
    else:
        resolved_model = model if model is not None else DEFAULT_MODEL
        resolved_voice = voice if voice is not None else DEFAULT_VOICE
        known_models = MODELS

    # A configured fallback ALWAYS routes to OpenRouter/Kokoro: default → Kokoro,
    # 'none' → disabled, fallback==primary → no-op (OpenRouter already primary).
    # A 'local' primary defaults the fallback to disabled (None) so a dead local
    # server never silently spends OpenRouter money; an explicit --fallback-model
    # re-enables it.
    fallback_default = None if provider is Provider.local else OPENROUTER_DEFAULT_MODEL
    resolved_fallback = _resolve_fallback(
        fallback_model, resolved_model, default=fallback_default
    )

    if resolved_model not in known_models:
        console.print(
            f"[yellow]Warning:[/yellow] '{resolved_model}' is not a known model "
            f"({', '.join(known_models)}). Proceeding anyway."
        )
    if resolved_fallback is not None and resolved_fallback not in OPENROUTER_MODELS:
        console.print(
            f"[yellow]Warning:[/yellow] fallback model '{resolved_fallback}' is "
            f"not a known OpenRouter model ({', '.join(OPENROUTER_MODELS)}). "
            "Proceeding anyway."
        )

    # --- Plan (no API calls) ------------------------------------------------
    try:
        document, chunks, estimate = pipeline.plan_only(
            input_file, title=title, author=author,
            max_chars=max_chars, model=resolved_model,
        )
    except LoaderError as exc:
        console.print(f"[red]Failed to load input:[/red] {exc}")
        raise typer.Exit(code=1)

    total_chars = sum(c.char_count for c in chunks)
    _print_plan(
        document, chunks, total_chars, estimate,
        provider.value, resolved_model, resolved_voice,
    )

    if dry_run:
        console.print("[dim]Dry run — no audio generated.[/dim]")
        raise typer.Exit()

    if not yes:
        proceed = typer.confirm(
            f"Synthesize {len(chunks)} chunk(s) (~${estimate:.2f})?",
            default=True,
        )
        if not proceed:
            console.print("Aborted.")
            raise typer.Exit()

    # --- Resolve credentials -----------------------------------------------
    # LocalConfig.from_env never raises MissingApiKeyError (a local server is
    # unauthenticated, so the key falls back to a placeholder) — there is no
    # missing-key exit path for the local provider.
    try:
        if provider is Provider.openrouter:
            config = OpenRouterConfig.from_env(
                model=resolved_model, voice=resolved_voice, base_url=base_url
            )
        elif provider is Provider.local:
            config = LocalConfig.from_env(
                model=resolved_model, voice=resolved_voice, base_url=base_url
            )
        else:
            config = StepFunConfig.from_env(
                model=resolved_model, voice=resolved_voice, base_url=base_url
            )
    except MissingApiKeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    # --- Run ----------------------------------------------------------------
    # Primary client factory: StepFun uses the pipeline's default (None);
    # OpenRouter and local supply their own. The factory takes the resolved
    # config so the pipeline never has to trust the caller to keep config and
    # client in sync.
    if provider is Provider.openrouter:
        def _make_client(cfg: TTSConfig) -> OpenRouterTTSClient:
            return OpenRouterTTSClient(config=cfg)
        client_factory = _make_client
    elif provider is Provider.local:
        def _make_local_client(cfg: TTSConfig) -> LocalTTSClient:
            return LocalTTSClient(config=cfg)
        client_factory = _make_local_client
    else:
        client_factory = None

    # Fallback ALWAYS goes to OpenRouter/Kokoro (voice af_heart), built lazily at
    # fallback time so a StepFun-only run never needs OPENROUTER_API_KEY upfront.
    # A missing key when the fallback actually triggers surfaces the original
    # error plus a skip note (handled by FallbackTTSClient).
    fallback_factory = None
    if resolved_fallback is not None:
        def _make_fallback() -> OpenRouterTTSClient:
            fb_config = OpenRouterConfig.from_env(model=resolved_fallback)
            return OpenRouterTTSClient(config=fb_config)
        fallback_factory = _make_fallback

    try:
        result = pipeline.run_pipeline(
            input_file,
            output_dir,
            config,
            title=title,
            author=author,
            max_chars=max_chars,
            split_by_chapter=split_by_chapter,
            client_factory=client_factory,
            fallback_factory=fallback_factory,
            resume=not no_resume,
            cleanup_cache=not keep_cache,
            concurrency=concurrency,
            rpm=rpm,
            console=console,
        )
    except KeyboardInterrupt:
        console.print(
            "\n[yellow]Interrupted.[/yellow] Completed chunks are cached — "
            "re-run the same command to resume from where it stopped "
            "(or pass --no-resume to start over)."
        )
        raise typer.Exit(code=130)
    except (LoaderError, RuntimeError) as exc:
        console.print(f"[red]Pipeline failed:[/red] {exc}")
        raise typer.Exit(code=1)

    _print_result(result)


@app.command("list-voices")
def list_voices(
    provider: Optional[Provider] = typer.Option(
        None, "--provider",
        case_sensitive=False,
        help="Show only one provider's voices (default: both).",
    ),
) -> None:
    """List the known voice catalogues, grouped by provider."""

    if provider is None or provider is Provider.stepfun:
        console.print(_voices_table("StepFun voices", VOICES, DEFAULT_VOICE))
        console.print(
            "[dim]These are StepFun voice IDs (not OpenAI names). The set may "
            "change — validate against a live call. Voice cloning is out of scope "
            "for v1.[/dim]"
        )
    if provider is None or provider is Provider.openrouter:
        console.print(
            _voices_table(
                "OpenRouter (Kokoro-82M) voices",
                KOKORO_VOICES,
                OPENROUTER_DEFAULT_VOICE,
            )
        )
        console.print(
            "[dim]Kokoro voice IDs: af_/am_ = US female/male, bf_/bm_ = UK. "
            "Parenthesised letters (A best → D) are hexgrad's quality grades. "
            "Use with --provider openrouter.[/dim]"
        )
    if provider is None or provider is Provider.local:
        console.print(
            _voices_table(
                "Local (Kokoro) voices", LOCAL_VOICES, LOCAL_DEFAULT_VOICE
            )
        )
        console.print(
            "[dim]A local Kokoro server serves the same Kokoro voice catalogue. "
            "Use with --provider local.[/dim]"
        )


@app.command("list-models")
def list_models(
    provider: Optional[Provider] = typer.Option(
        None, "--provider",
        case_sensitive=False,
        help="Show only one provider's models (default: both).",
    ),
) -> None:
    """List available models and pricing, grouped by provider."""

    if provider is None or provider is Provider.stepfun:
        console.print(_models_table("StepFun models", MODELS, DEFAULT_MODEL))
    if provider is None or provider is Provider.openrouter:
        console.print(
            _models_table(
                "OpenRouter models", OPENROUTER_MODELS, OPENROUTER_DEFAULT_MODEL
            )
        )
    if provider is None or provider is Provider.local:
        console.print(
            _models_table(
                "Local (Kokoro) models", LOCAL_MODELS, LOCAL_DEFAULT_MODEL
            )
        )
        console.print(
            "[dim]Self-hosted, so priced at $0.0000 — you pay only for your own "
            "compute. Use with --provider local (default http://127.0.0.1:8880/v1).[/dim]"
        )


# -- output helpers ---------------------------------------------------------


def _format_price(price_per_10k: float) -> str:
    """Format a per-10k-char price with enough precision.

    StepFun's prices are cents-scale ($0.85, $0.40); Kokoro is sub-cent
    ($0.0062), which ``.2f`` would collapse to a misleading ``$0.01``. Show two
    decimals for the former and four for the latter so both read accurately.
    """

    return f"${price_per_10k:.2f}" if price_per_10k >= 0.10 else f"${price_per_10k:.4f}"


def _models_table(title: str, models, default_model: str) -> Table:
    table = Table(title=title)
    table.add_column("Model")
    table.add_column("USD / 10k chars", justify="right")
    table.add_column("Default", justify="center")
    table.add_column("Description")
    for name, info in models.items():
        table.add_row(
            name,
            _format_price(info.price_per_10k_chars),
            "✓" if name == default_model else "",
            info.description,
        )
    return table


def _voices_table(title: str, voices, default_voice: str) -> Table:
    table = Table(title=title)
    table.add_column("Voice ID")
    table.add_column("Description")
    table.add_column("Default", justify="center")
    for voice_id, description in voices.items():
        table.add_row(
            voice_id, description, "✓" if voice_id == default_voice else ""
        )
    return table


def _print_plan(
    document, chunks, total_chars, estimate, provider, model, voice
) -> None:
    table = Table(title="Conversion plan", show_header=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Title", document.title)
    table.add_row("Author", document.author)
    table.add_row("Chapters", str(len(document.chapters)))
    table.add_row("Chunks", str(len(chunks)))
    table.add_row("Characters", f"{total_chars:,}")
    table.add_row("Provider", provider)
    table.add_row("Model", model)
    table.add_row("Voice", voice)
    table.add_row("Est. cost", f"${estimate:.2f}")
    console.print(table)


def _print_result(result) -> None:
    console.print("[bold green]Done.[/bold green]")
    for path in result.assembly.output_files:
        console.print(f"  • {path}")
    console.print(f"Estimated cost: ${result.estimated_cost_usd:.2f}")


if __name__ == "__main__":  # pragma: no cover
    app()
