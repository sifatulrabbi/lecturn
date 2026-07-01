"""End-to-end pipeline.

Wires the stages together: load -> clean -> chunk -> synthesize -> assemble.
Blocks the terminal, showing a progress bar during the TTS stage (the only slow,
network-bound step). No job queue, no background worker (PLAN.md "Key Design
Decisions").

The synthesize stage is sequential by default but can run a bounded number of
requests concurrently (``concurrency``), throttled to a requests-per-minute cap
(``rpm``) so it never exceeds the account's rate limits. Concurrency speeds up
wall-clock time WITHOUT changing total cost (same characters synthesized).
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# Sensible default for the synth stage. The library default is 1 (sequential);
# the CLI raises it. Concurrency above the account's per-model limit only trips
# rate limits, so callers should keep it at/under that (StepFun: 5).
DEFAULT_CONCURRENCY: int = 1
MAX_USEFUL_CONCURRENCY: int = 5
# Requests-per-minute ceiling honoured by the throttle (StepFun per-model: 10).
DEFAULT_RPM: int = 10


@dataclass
class PipelineResult:
    document: Document
    chunks: list[Chunk]
    assembly: AssemblyResult
    estimated_cost_usd: float


class _RateLimiter:
    """Blocks callers so no more than ``max_calls`` proceed per ``period`` seconds.

    A sliding-window limiter shared across worker threads: it caps how many
    requests *start* per minute, independent of how many run concurrently. With
    ``max_calls <= 0`` it is a no-op (unlimited).
    """

    def __init__(self, max_calls: int, period: float = 60.0) -> None:
        self.max_calls = max_calls
        self.period = period
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self.max_calls <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                cutoff = now - self.period
                while self._times and self._times[0] <= cutoff:
                    self._times.popleft()
                if len(self._times) < self.max_calls:
                    self._times.append(now)
                    return
                wait = self._times[0] + self.period - now
            time.sleep(max(wait, 0.0))


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
    concurrency: int = DEFAULT_CONCURRENCY,
    rpm: int = DEFAULT_RPM,
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
        chunks, client, cache_dir, config,
        resume=resume, concurrency=concurrency, rpm=rpm, console=console,
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


def _chunk_fingerprint(config: StepFunConfig, text: str) -> str:
    """Short hash identifying the exact audio a chunk would produce.

    Covers everything that determines the rendered audio: the voice, the model,
    the response format, and the chunk text. Embedding it in the cache filename
    means resume reuses a cached chunk ONLY when it matches the current run —
    change the voice/model/text and the fingerprint (and filename) changes, so
    stale audio is never silently reused.
    """

    h = hashlib.sha1()
    for part in (config.voice, config.model, config.response_format):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    h.update(text.encode("utf-8"))
    return h.hexdigest()[:12]


def _chunk_cache_path(cache_dir: Path, chunk: Chunk, config: StepFunConfig) -> Path:
    fp = _chunk_fingerprint(config, chunk.text)
    return cache_dir / f"chunk_{chunk.index:05d}_{fp}.mp3"


def _is_playable_mp3(path: Path) -> bool:
    """Return True only if ``path`` is a non-empty, parseable MP3.

    Resume must never trust a cache file just because it exists and is
    non-empty — a corrupt file would poison assembly. Atomic writes prevent
    partial files going forward; this check is the belt-and-suspenders that also
    rejects empty/garbage files from any other cause.
    """

    try:
        if path.stat().st_size <= 0:
            return False
    except OSError:
        return False
    try:
        from mutagen.mp3 import MP3  # type: ignore

        return float(getattr(MP3(path).info, "length", 0.0)) > 0.0
    except Exception:
        return False


def _make_progress(console: Console) -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("chunks"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    )


def _synthesize_all(
    chunks: list[Chunk],
    client: StepFunTTSClient,
    cache_dir: Path,
    config: StepFunConfig,
    *,
    resume: bool,
    concurrency: int = DEFAULT_CONCURRENCY,
    rpm: int = DEFAULT_RPM,
    console: Console,
) -> dict[int, Path]:
    """Synthesize every chunk, returning a map of chunk index -> file path.

    Resume: chunks whose fingerprinted cache file already exists and is a valid
    MP3 are reused (not re-synthesized, not re-billed).

    Concurrency: ``concurrency`` requests run at once (``1`` = strictly
    sequential, the safe default), throttled to ``rpm`` request-starts per
    minute. Concurrency changes wall-clock time only — total characters (and
    therefore cost) are unchanged. The number in flight never exceeds
    ``concurrency`` and starts never exceed ``rpm`` (enforced by
    ``test_synthesis_concurrency_is_bounded``).
    """

    chunk_files: dict[int, Path] = {}
    todo: list[tuple[Chunk, Path]] = []
    for chunk in chunks:
        out_path = _chunk_cache_path(cache_dir, chunk, config)
        if resume and out_path.exists() and _is_playable_mp3(out_path):
            chunk_files[chunk.index] = out_path  # reuse cached audio
        else:
            todo.append((chunk, out_path))

    already_done = len(chunks) - len(todo)
    with _make_progress(console) as progress:
        task = progress.add_task("Synthesizing", total=len(chunks))
        if already_done:
            progress.advance(task, advance=already_done)
        if not todo:
            return chunk_files

        limiter = _RateLimiter(rpm)
        if concurrency <= 1:
            for chunk, out_path in todo:
                limiter.acquire()
                client.synthesize_chunk(chunk, out_path)
                chunk_files[chunk.index] = out_path
                progress.advance(task)
        else:
            _synthesize_concurrent(
                todo, chunk_files, client, limiter, progress, task,
                concurrency=concurrency,
            )

    return chunk_files


def _synthesize_concurrent(
    todo: list[tuple[Chunk, Path]],
    chunk_files: dict[int, Path],
    client: StepFunTTSClient,
    limiter: _RateLimiter,
    progress: Progress,
    task,
    *,
    concurrency: int,
) -> None:
    """Run the pending chunks through a bounded thread pool + RPM throttle.

    Each worker blocks on the shared rate limiter before its request, so no more
    than ``concurrency`` run at once and no more than the limiter's cap start per
    minute. On the first failure (or Ctrl-C) pending work is cancelled; requests
    already in flight are allowed to finish so their audio is cached for resume.
    """

    def work(chunk: Chunk, out_path: Path) -> tuple[int, Path]:
        limiter.acquire()
        client.synthesize_chunk(chunk, out_path)
        return chunk.index, out_path

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(work, c, p): c for c, p in todo}
        try:
            for future in as_completed(futures):
                index, out_path = future.result()
                chunk_files[index] = out_path
                progress.advance(task)
        except BaseException:
            for future in futures:
                future.cancel()  # drop not-yet-started work; in-flight finishes
            raise


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
