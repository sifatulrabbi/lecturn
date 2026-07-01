"""End-to-end synchronous pipeline.

Wires the stages together: load -> clean -> chunk -> synthesize -> assemble.
Runs synchronously and blocks the terminal, showing a progress bar during the
TTS stage (the only slow, network-bound step). No job queue, no background
worker (PLAN.md "Key Design Decisions").
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from textbook_audiobook import assembler, chunker, cleaner
from textbook_audiobook.assembler import AssemblyResult
from textbook_audiobook.config import StepFunConfig, estimate_cost
from textbook_audiobook.loaders import load_document
from textbook_audiobook.models import Chunk, Document
from textbook_audiobook.tts import StepFunTTSClient


@dataclass
class PipelineResult:
    document: Document
    chunks: list[Chunk]
    assembly: AssemblyResult
    estimated_cost_usd: float


def _plan(document: Document, max_chars: int) -> tuple[Document, list[Chunk]]:
    cleaned = cleaner.clean_document(document)
    chunks = chunker.chunk_document(cleaned, max_chars=max_chars)
    return cleaned, chunks


def run_pipeline(
    input_path: Path,
    output_dir: Path,
    config: StepFunConfig,
    *,
    title: str | None = None,
    author: str | None = None,
    max_chars: int,
    split_by_chapter: bool = False,
    fallback_model: str | None = None,
    cache_dir: Path | None = None,
    resume: bool = True,
    console: Console | None = None,
) -> PipelineResult:
    """Execute the full pipeline and return a :class:`PipelineResult`."""

    console = console or Console()

    document = load_document(input_path, title=title, author=author)
    cleaned, chunks = _plan(document, max_chars)

    if not chunks:
        raise RuntimeError(
            "No narratable text was produced from the input after cleaning."
        )

    cache_dir = cache_dir or (output_dir / ".audiobook_cache" / cleaned.slug)
    cache_dir.mkdir(parents=True, exist_ok=True)

    client = StepFunTTSClient(config=config, fallback_model=fallback_model)
    chunk_files = _synthesize_all(
        chunks, client, cache_dir, resume=resume, console=console
    )

    if client.stats.fallbacks:
        console.print(
            f"[yellow]Note:[/yellow] fell back from '{config.model}' to "
            f"'{client.active_model}' after the primary model was rejected."
        )

    console.print("[bold]Assembling audio…[/bold]")
    assembly = assembler.assemble(
        cleaned,
        chunks,
        chunk_files,
        output_dir,
        split_by_chapter=split_by_chapter,
    )

    # Cost reflects the model actually used (may be the fallback).
    estimated = estimate_cost(client.stats.characters, client.active_model)
    return PipelineResult(
        document=cleaned,
        chunks=chunks,
        assembly=assembly,
        estimated_cost_usd=estimated,
    )


def _chunk_cache_path(cache_dir: Path, chunk: Chunk) -> Path:
    return cache_dir / f"chunk_{chunk.index:05d}.mp3"


def _synthesize_all(
    chunks: list[Chunk],
    client: StepFunTTSClient,
    cache_dir: Path,
    *,
    resume: bool,
    console: Console,
) -> dict[int, Path]:
    """Synthesize every chunk, returning a map of chunk index -> file path.

    IMPORTANT — synthesis is deliberately SEQUENTIAL: chunks are processed one
    at a time in a plain loop, and each ``synthesize_chunk`` call blocks until
    its network request completes before the next begins. There is never more
    than one TTS request in flight. Do NOT parallelise this (thread pool,
    asyncio, ``executor.map``, etc.): concurrent requests would multiply usage
    against StepFun's per-account quota and can trip rate limits. The
    ``test_synthesis_runs_sequentially`` regression test enforces this.
    """

    chunk_files: dict[int, Path] = {}

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("chunks"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    )

    with progress:
        task = progress.add_task("Synthesizing", total=len(chunks))
        for chunk in chunks:
            out_path = _chunk_cache_path(cache_dir, chunk)
            if resume and out_path.exists() and out_path.stat().st_size > 0:
                # Reuse previously synthesized audio (resume support).
                chunk_files[chunk.index] = out_path
                progress.advance(task)
                continue

            client.synthesize_chunk(chunk, out_path)
            chunk_files[chunk.index] = out_path
            progress.advance(task)

    return chunk_files


def plan_only(
    input_path: Path,
    *,
    title: str | None,
    author: str | None,
    max_chars: int,
    model: str,
) -> tuple[Document, list[Chunk], float]:
    """Load + clean + chunk without calling the API (for --dry-run/estimate)."""

    document = load_document(input_path, title=title, author=author)
    cleaned, chunks = _plan(document, max_chars)
    total_chars = sum(c.char_count for c in chunks)
    return cleaned, chunks, estimate_cost(total_chars, model)
