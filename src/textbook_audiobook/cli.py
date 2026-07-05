"""Command-line interface (Typer).

Subcommands:
  convert       Convert an input file into an audiobook (the main command).
  list-voices   Show the known voice catalogues (per provider).
  list-models   Show available models and pricing (per provider).

Two TTS providers are selectable via ``--provider``: ``stepfun`` (the default,
premium/economy StepFun models) and ``openrouter`` (Kokoro-82M). ``--model`` /
``--voice`` / ``--fallback-model`` default per provider — pass them to override.
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
    ECONOMY_MODEL,
    HARD_CHAR_LIMIT,
    KOKORO_VOICES,
    MODELS,
    OPENROUTER_DEFAULT_MODEL,
    OPENROUTER_DEFAULT_VOICE,
    OPENROUTER_MODELS,
    VOICES,
    MissingApiKeyError,
    OpenRouterConfig,
    StepFunConfig,
)
from textbook_audiobook.loaders import LoaderError, SUPPORTED_EXTENSIONS
from textbook_audiobook import pipeline
from textbook_audiobook.pipeline import DEFAULT_RPM, MAX_USEFUL_CONCURRENCY
from textbook_audiobook.tts import OpenRouterTTSClient


class Provider(str, Enum):
    """TTS provider selector.

    ``stepfun`` is the original (and default) provider; ``openrouter`` narrates
    with Kokoro-82M. A ``str`` Enum so Typer renders ``[stepfun|openrouter]``
    choices and validates them for us.
    """

    stepfun = "stepfun"
    openrouter = "openrouter"


app = typer.Typer(
    add_completion=False,
    help=(
        "Convert textbooks (PDF/EPUB/TXT/MD) into narrated audiobooks via "
        "StepFun or OpenRouter (Kokoro) TTS."
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
        help="TTS provider: 'stepfun' (default) or 'openrouter' (Kokoro-82M).",
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m",
        help=(
            "TTS model. Defaults to the provider's best-quality model "
            "(stepfun: stepaudio-2.5-tts, openrouter: hexgrad/kokoro-82m)."
        ),
    ),
    voice: Optional[str] = typer.Option(
        None, "--voice",
        help=(
            "Voice ID. Defaults per provider "
            "(stepfun: lively-girl, openrouter: af_heart)."
        ),
    ),
    fallback_model: Optional[str] = typer.Option(
        None, "--fallback-model",
        help=(
            "Model to retry with if the primary model is rejected (e.g. quota "
            "or entitlement). StepFun only — defaults to step-tts-2; OpenRouter "
            "has no fallback. Pass 'none' to disable."
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
        None, "--base-url", help="Override the provider's API base URL."
    ),
    no_resume: bool = typer.Option(
        False, "--no-resume",
        help=(
            "Ignore any cached audio and re-synthesize every chunk from scratch. "
            "By default a run resumes from cache; switching --model reuses the "
            "cache (only a voice or text change invalidates it)."
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
        console.print(
            f"[red]--max-chars {max_chars} exceeds StepFun's hard limit of "
            f"{HARD_CHAR_LIMIT}.[/red]"
        )
        raise typer.Exit(code=2)
    if max_chars <= 0:
        console.print("[red]--max-chars must be positive.[/red]")
        raise typer.Exit(code=2)

    if concurrency < 1:
        console.print("[red]--concurrency must be at least 1.[/red]")
        raise typer.Exit(code=2)
    if concurrency > MAX_USEFUL_CONCURRENCY:
        console.print(
            f"[yellow]Warning:[/yellow] --concurrency {concurrency} exceeds "
            f"StepFun's per-model concurrency limit ({MAX_USEFUL_CONCURRENCY}); "
            "extra requests will just queue behind the --rpm throttle."
        )
    if rpm < 0:
        console.print("[red]--rpm cannot be negative (use 0 to disable).[/red]")
        raise typer.Exit(code=2)

    # --- Resolve per-provider defaults --------------------------------------
    # Each flag defaults to None so we can tell "not given" from an explicit
    # value and fill in the right provider default here. The StepFun branch
    # reproduces the pre-provider behaviour byte-for-byte.
    if provider is Provider.openrouter:
        resolved_model = model if model is not None else OPENROUTER_DEFAULT_MODEL
        resolved_voice = voice if voice is not None else OPENROUTER_DEFAULT_VOICE
        default_fallback: str | None = None
        known_models = OPENROUTER_MODELS
    else:
        resolved_model = model if model is not None else DEFAULT_MODEL
        resolved_voice = voice if voice is not None else DEFAULT_VOICE
        default_fallback = ECONOMY_MODEL
        known_models = MODELS

    # Fallback is a StepFun-tier concept: an economy model to retry with. When
    # not given, use the provider default (StepFun: economy; OpenRouter: none).
    if fallback_model is None:
        resolved_fallback = default_fallback
    elif fallback_model.strip().lower() in {"none", ""}:
        resolved_fallback = None
    else:
        resolved_fallback = fallback_model
    if resolved_fallback == resolved_model:
        resolved_fallback = None  # a fallback equal to the primary is a no-op

    if resolved_model not in known_models:
        console.print(
            f"[yellow]Warning:[/yellow] '{resolved_model}' is not a known model "
            f"({', '.join(known_models)}). Proceeding anyway."
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
    try:
        if provider is Provider.openrouter:
            config = OpenRouterConfig.from_env(
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
    # StepFun uses the pipeline's default client factory (unchanged path);
    # OpenRouter supplies its own client via the slice-1 client_factory seam.
    # ``fallback_model`` is only consulted by the default (StepFun) factory.
    if provider is Provider.openrouter:
        def _make_client() -> OpenRouterTTSClient:
            return OpenRouterTTSClient(
                config=config, fallback_model=resolved_fallback
            )
        client_factory = _make_client
    else:
        client_factory = None

    try:
        result = pipeline.run_pipeline(
            input_file,
            output_dir,
            config,
            title=title,
            author=author,
            max_chars=max_chars,
            split_by_chapter=split_by_chapter,
            fallback_model=resolved_fallback,
            client_factory=client_factory,
            resume=not no_resume,
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
