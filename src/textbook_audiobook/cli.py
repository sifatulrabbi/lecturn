"""Command-line interface (Typer).

Subcommands:
  convert         Convert an input file into an audiobook (the main command).
  list-providers  Show the available TTS providers.
  list-voices     Show a provider's known voice catalogue.
  list-models     Show a provider's available models and pricing.

Every provider-facing command requires ``--provider`` — there is no default. The
per-provider defaults (model, voice, char limit, RPM, fallback) are filled in
once the provider is resolved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from textbook_audiobook import __version__
from textbook_audiobook.config import MissingApiKeyError, TTSConfig
from textbook_audiobook.loaders import LoaderError, SUPPORTED_EXTENSIONS
from textbook_audiobook.providers import (
    UnknownProviderError,
    available_providers,
    get_provider,
)
from textbook_audiobook import pipeline

app = typer.Typer(
    add_completion=False,
    help=(
        "Convert textbooks (PDF/EPUB/TXT/MD) into narrated audiobooks via a TTS "
        "provider (StepFun, OpenRouter, …). Pick one with --provider."
    ),
    no_args_is_help=True,
)
console = Console()

_PROVIDER_OPTION_HELP = (
    "TTS provider (required). One of: " + ", ".join(available_providers()) + "."
)


def _resolve_provider(name: str):
    """Resolve a provider by name or exit 2 with the list of valid names."""

    try:
        return get_provider(name)
    except UnknownProviderError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)


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
    provider: str = typer.Option(
        ..., "--provider", "-p", help=_PROVIDER_OPTION_HELP,
    ),
    output_dir: Path = typer.Option(
        Path("output"), "--output", "-o", help="Directory for output MP3(s)."
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m",
        help="TTS model. Defaults to the provider's best-quality model.",
    ),
    voice: Optional[str] = typer.Option(
        None, "--voice", help="Voice name. Defaults to the provider's default voice."
    ),
    fallback_model: Optional[str] = typer.Option(
        None, "--fallback-model",
        help=(
            "Model to retry with if the primary model is rejected (e.g. quota "
            "or entitlement). Defaults to the provider's fallback (StepFun: the "
            "economy model; OpenRouter: none, since its voices are "
            "model-specific). Pass 'none' to disable."
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
    max_chars: Optional[int] = typer.Option(
        None, "--max-chars",
        help="Max chars per chunk. Defaults to (and is capped by) the provider's hard limit.",
    ),
    base_url: Optional[str] = typer.Option(
        None, "--base-url", help="Override the provider base URL."
    ),
    no_resume: bool = typer.Option(
        False, "--no-resume",
        help=(
            "Ignore any cached audio and re-synthesize every chunk from scratch. "
            "By default a run resumes from cache; switching --model reuses the "
            "cache (only a provider, voice, or text change invalidates it)."
        ),
    ),
    concurrency: int = typer.Option(
        3, "--concurrency", "-c",
        help=(
            "Number of chunks to synthesize in parallel. Speeds up the run at "
            "the same cost. Keep at/under your provider's per-model concurrency "
            "limit. Use 1 for strictly sequential."
        ),
    ),
    rpm: Optional[int] = typer.Option(
        None, "--rpm",
        help=(
            "Max requests started per minute (throttle). Defaults to the "
            "provider's per-model RPM guidance. 0 disables the throttle."
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

    prov = _resolve_provider(provider)

    # Fill provider-specific defaults for any option left unset.
    model = model or prov.default_model
    voice = voice or prov.default_voice
    max_chars = max_chars if max_chars is not None else prov.hard_char_limit
    rpm = rpm if rpm is not None else prov.default_rpm

    if max_chars > prov.hard_char_limit:
        console.print(
            f"[red]--max-chars {max_chars} exceeds {prov.label}'s hard limit of "
            f"{prov.hard_char_limit}.[/red]"
        )
        raise typer.Exit(code=2)
    if max_chars <= 0:
        console.print("[red]--max-chars must be positive.[/red]")
        raise typer.Exit(code=2)

    if concurrency < 1:
        console.print("[red]--concurrency must be at least 1.[/red]")
        raise typer.Exit(code=2)
    if concurrency > prov.concurrency_limit:
        console.print(
            f"[yellow]Warning:[/yellow] --concurrency {concurrency} exceeds "
            f"{prov.label}'s per-model concurrency limit ({prov.concurrency_limit}); "
            "extra requests will just queue behind the --rpm throttle."
        )
    if rpm < 0:
        console.print("[red]--rpm cannot be negative (use 0 to disable).[/red]")
        raise typer.Exit(code=2)

    if model not in prov.models:
        console.print(
            f"[yellow]Warning:[/yellow] '{model}' is not a known model for "
            f"{prov.label} ({', '.join(prov.models)}). Proceeding anyway."
        )

    # --- Plan (no API calls) ------------------------------------------------
    try:
        document, chunks, estimate = pipeline.plan_only(
            input_file, provider=prov, title=title, author=author,
            max_chars=max_chars, model=model,
        )
    except LoaderError as exc:
        console.print(f"[red]Failed to load input:[/red] {exc}")
        raise typer.Exit(code=1)

    total_chars = sum(c.char_count for c in chunks)
    _print_plan(document, chunks, total_chars, estimate, model, prov)

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
        config = TTSConfig.resolve(
            prov, model=model, voice=voice, base_url=base_url
        )
    except MissingApiKeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    # --- Resolve fallback model --------------------------------------------
    if fallback_model is None:
        resolved_fallback: str | None = prov.default_fallback_model
    elif fallback_model.strip().lower() in {"none", ""}:
        resolved_fallback = None
    else:
        resolved_fallback = fallback_model
    if resolved_fallback == model:
        resolved_fallback = None

    # --- Run ----------------------------------------------------------------
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


@app.command("list-providers")
def list_providers() -> None:
    """List the available TTS providers."""

    table = Table(title="TTS providers")
    table.add_column("Name")
    table.add_column("Label")
    table.add_column("Base URL")
    table.add_column("Default model")
    table.add_column("API key env")
    for name in available_providers():
        prov = get_provider(name)
        table.add_row(
            prov.name,
            prov.label,
            prov.default_base_url,
            prov.default_model,
            " / ".join(prov.api_key_env),
        )
    console.print(table)
    console.print(
        "[dim]Select one with --provider. See its models/voices with "
        "`list-models --provider <name>` / `list-voices --provider <name>`.[/dim]"
    )


@app.command("list-voices")
def list_voices(
    provider: str = typer.Option(
        ..., "--provider", "-p", help=_PROVIDER_OPTION_HELP,
    ),
) -> None:
    """List a provider's known voice catalogue."""

    prov = _resolve_provider(provider)
    table = Table(title=f"{prov.label} voices")
    table.add_column("Voice ID")
    table.add_column("Description")
    table.add_column("Default", justify="center")
    for voice_id, description in prov.voices.items():
        table.add_row(
            voice_id, description, "✓" if voice_id == prov.default_voice else ""
        )
    console.print(table)
    if prov.name == "openrouter":
        console.print(
            "[dim]Voices on OpenRouter are model-specific — the list above is for "
            f"the default model ({prov.default_model}). For another model, see "
            "its page at https://openrouter.ai/<model-id>.[/dim]"
        )
    else:
        console.print(
            "[dim]These are provider voice IDs (not OpenAI names). The set may "
            "change — validate against a live call. Access can be per-account.[/dim]"
        )


@app.command("list-models")
def list_models(
    provider: str = typer.Option(
        ..., "--provider", "-p", help=_PROVIDER_OPTION_HELP,
    ),
) -> None:
    """List a provider's available models and pricing."""

    prov = _resolve_provider(provider)
    table = Table(title=f"{prov.label} models")
    table.add_column("Model")
    table.add_column("USD / 10k chars", justify="right")
    table.add_column("Default", justify="center")
    table.add_column("Description")
    for name, info in prov.models.items():
        table.add_row(
            name,
            f"${info.price_per_10k_chars:.3f}",
            "✓" if name == prov.default_model else "",
            info.description,
        )
    console.print(table)
    if prov.name == "openrouter":
        console.print(
            "[dim]OpenRouter bills per token; the prices above are approximate "
            "per-character estimates for planning only.[/dim]"
        )


# -- output helpers ---------------------------------------------------------


def _print_plan(document, chunks, total_chars, estimate, model, provider) -> None:
    table = Table(title="Conversion plan", show_header=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Title", document.title)
    table.add_row("Author", document.author)
    table.add_row("Provider", provider.label)
    table.add_row("Chapters", str(len(document.chapters)))
    table.add_row("Chunks", str(len(chunks)))
    table.add_row("Characters", f"{total_chars:,}")
    table.add_row("Model", model)
    table.add_row("Est. cost", f"${estimate:.2f}")
    console.print(table)


def _print_result(result) -> None:
    console.print("[bold green]Done.[/bold green]")
    for path in result.assembly.output_files:
        console.print(f"  • {path}")
    console.print(f"Estimated cost: ${result.estimated_cost_usd:.2f}")


if __name__ == "__main__":  # pragma: no cover
    app()
