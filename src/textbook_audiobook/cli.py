"""Command-line interface (Typer).

Subcommands:
  convert       Convert an input file into an audiobook (the main command).
  list-voices   Show the known voice catalogue.
  list-models   Show available models and pricing.
"""

from __future__ import annotations

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
    MODELS,
    VOICES,
    MissingApiKeyError,
    StepFunConfig,
)
from textbook_audiobook.loaders import LoaderError, SUPPORTED_EXTENSIONS
from textbook_audiobook import pipeline

app = typer.Typer(
    add_completion=False,
    help="Convert textbooks (PDF/EPUB/TXT/MD) into narrated audiobooks via StepFun TTS.",
    no_args_is_help=True,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"textbook-audiobook {__version__}")
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
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", "-m",
        help="TTS model. Default is the best-quality model.",
    ),
    voice: str = typer.Option(
        DEFAULT_VOICE, "--voice", help="Voice name."
    ),
    fallback_model: str = typer.Option(
        ECONOMY_MODEL, "--fallback-model",
        help=(
            "Model to retry with if the primary model is rejected (e.g. quota "
            "or entitlement). Pass 'none' to disable fallback."
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
        None, "--base-url", help="Override the StepFun base URL."
    ),
    no_resume: bool = typer.Option(
        False, "--no-resume",
        help="Re-synthesize all chunks even if cached audio exists.",
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

    if model not in MODELS:
        console.print(
            f"[yellow]Warning:[/yellow] '{model}' is not a known model "
            f"({', '.join(MODELS)}). Proceeding anyway."
        )

    # --- Plan (no API calls) ------------------------------------------------
    try:
        document, chunks, estimate = pipeline.plan_only(
            input_file, title=title, author=author,
            max_chars=max_chars, model=model,
        )
    except LoaderError as exc:
        console.print(f"[red]Failed to load input:[/red] {exc}")
        raise typer.Exit(code=1)

    total_chars = sum(c.char_count for c in chunks)
    _print_plan(document, chunks, total_chars, estimate, model)

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
        config = StepFunConfig.from_env(
            model=model, voice=voice, base_url=base_url
        )
    except MissingApiKeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    # --- Run ----------------------------------------------------------------
    resolved_fallback: str | None = fallback_model
    if fallback_model.strip().lower() in {"none", ""} or fallback_model == model:
        resolved_fallback = None

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
            console=console,
        )
    except (LoaderError, RuntimeError) as exc:
        console.print(f"[red]Pipeline failed:[/red] {exc}")
        raise typer.Exit(code=1)

    _print_result(result)


@app.command("list-voices")
def list_voices() -> None:
    """List the known StepFun voice catalogue."""

    table = Table(title="StepFun voices")
    table.add_column("Voice ID")
    table.add_column("Description")
    table.add_column("Default", justify="center")
    for voice_id, description in VOICES.items():
        table.add_row(
            voice_id, description, "✓" if voice_id == DEFAULT_VOICE else ""
        )
    console.print(table)
    console.print(
        "[dim]These are StepFun voice IDs (not OpenAI names). The set may "
        "change — validate against a live call. Voice cloning is out of scope "
        "for v1.[/dim]"
    )


@app.command("list-models")
def list_models() -> None:
    """List available models and pricing."""

    table = Table(title="TTS models")
    table.add_column("Model")
    table.add_column("USD / 10k chars", justify="right")
    table.add_column("Default", justify="center")
    table.add_column("Description")
    for name, info in MODELS.items():
        table.add_row(
            name,
            f"${info.price_per_10k_chars:.2f}",
            "✓" if name == DEFAULT_MODEL else "",
            info.description,
        )
    console.print(table)


# -- output helpers ---------------------------------------------------------


def _print_plan(document, chunks, total_chars, estimate, model) -> None:
    table = Table(title="Conversion plan", show_header=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Title", document.title)
    table.add_row("Author", document.author)
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
