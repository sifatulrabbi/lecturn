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
from collections.abc import Callable
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
from textbook_audiobook.config import TTSConfig, estimate_cost
from textbook_audiobook.loaders import load_document
from textbook_audiobook.models import Chunk, Document
from textbook_audiobook.tts import (
    FallbackTTSClient,
    StepFunTTSClient,
    _BaseTTSClient,
)

# Sensible default for the synth stage. The library default is 1 (sequential);
# the CLI raises it. Concurrency above the account's per-model limit only trips
# rate limits, so callers should keep it at/under that (StepFun: 5).
DEFAULT_CONCURRENCY: int = 1
MAX_USEFUL_CONCURRENCY: int = 5
# Requests-per-minute ceiling honoured by the throttle (StepFun per-model: 10).
DEFAULT_RPM: int = 10
# Name of the per-book resume-cache directory created under the output dir.
_CACHE_DIRNAME: str = ".audiobook_cache"


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
    config: TTSConfig,
    *,
    title: str | None = None,
    author: str | None = None,
    max_chars: int,
    split_by_chapter: bool = False,
    client_factory: Callable[[TTSConfig], _BaseTTSClient] | None = None,
    fallback_factory: Callable[[], _BaseTTSClient] | None = None,
    cache_dir: Path | None = None,
    resume: bool = True,
    cleanup_cache: bool = True,
    concurrency: int = DEFAULT_CONCURRENCY,
    rpm: int = DEFAULT_RPM,
    console: Console | None = None,
) -> PipelineResult:
    """Execute the full pipeline and return a :class:`PipelineResult`.

    The primary TTS client is built by ``client_factory``, a callable taking the
    ``config`` and returning a built
    :class:`~textbook_audiobook.tts._BaseTTSClient` — so the client's config can
    never drift from the one used to fingerprint the cache. When omitted, the
    pipeline builds a :class:`~textbook_audiobook.tts.StepFunTTSClient` from
    ``config`` (the default StepFun path).

    ``fallback_factory`` (when given) is wrapped around the primary in a
    :class:`~textbook_audiobook.tts.FallbackTTSClient`: on a fallback-eligible
    failure the run switches, once and one-way, to that (OpenRouter/Kokoro)
    client. Because the switch changes the voice mid-book, each chunk's cache
    fingerprint is derived from ``client.active_config`` at dispatch time, not
    from ``config`` — the cache always reflects what actually spoke each chunk.

    ``cleanup_cache`` (default ``True``) deletes this run's resume cache once the
    pipeline completes *fully* — every chunk synthesized AND every output file
    written. Any exception, partial failure, or interrupt leaves the cache intact
    so ``resume`` still works. Pass ``cleanup_cache=False`` (the CLI's
    ``--keep-cache``) to preserve it; the library default matches the CLI default.
    """

    console = console or Console()

    document = load_document(input_path, title=title, author=author)
    cleaned, chunks = _plan(document, max_chars)

    if not chunks:
        raise RuntimeError(
            "No narratable text was produced from the input after cleaning."
        )

    cache_dir = cache_dir or (output_dir / _CACHE_DIRNAME / cleaned.slug)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if client_factory is None:
        primary = StepFunTTSClient(config=config)
    else:
        primary = client_factory(config)
    client = FallbackTTSClient(primary=primary, fallback_factory=fallback_factory)

    chunk_files = _synthesize_all(
        chunks, client, cache_dir,
        resume=resume, concurrency=concurrency, rpm=rpm, console=console,
    )

    if client.stats.fallbacks:
        console.print(
            f"[yellow]Note:[/yellow] fell back from '{config.model}' to "
            f"'{client.active_model}' (OpenRouter/Kokoro) after the primary "
            "provider was rejected."
        )

    console.print("[bold]Assembling audio…[/bold]")
    assembly = assembler.assemble(
        cleaned,
        chunks,
        chunk_files,
        output_dir,
        split_by_chapter=split_by_chapter,
    )

    # A fully successful run (every chunk synthesized above AND every output file
    # written by assemble) makes the resume cache dead weight — drop it unless the
    # caller opted to keep it. Only reached because ``assemble`` returned without
    # raising, so any earlier exception/partial failure/interrupt skips this and
    # leaves the cache for ``resume``.
    if cleanup_cache and assembly.output_files and all(
        p.exists() for p in assembly.output_files
    ):
        _cleanup_cache(cache_dir, chunk_files, console)

    # Attribute each chunk's characters to the model that actually synthesized
    # it, then sum per model. A mid-book cross-provider fallback bills the
    # earlier chunks at the primary's rate and the later ones at the fallback's
    # (a >100x ratio for StepFun -> Kokoro), so a single post-hoc estimate at the
    # final model's rate would be badly wrong. On a full resume-cache hit nothing
    # was billed, so the map is empty and the estimate is 0.0.
    estimated = sum(
        (
            estimate_cost(chars, model)
            for model, chars in client.stats.characters_by_model.items()
        ),
        0.0,
    )
    return PipelineResult(
        document=cleaned,
        chunks=chunks,
        assembly=assembly,
        estimated_cost_usd=estimated,
    )


def _chunk_fingerprint(config: TTSConfig, text: str) -> str:
    """Short hash identifying a cached chunk by narrator + content.

    Deliberately keyed on the ``voice``, response ``format``, and chunk text —
    NOT the model and NOT the provider. A single book can legitimately span
    models *and providers* (e.g. the automatic StepFun→OpenRouter/Kokoro fallback
    on a quota outage), so switching ``--model`` must not invalidate the cache;
    Kokoro voice IDs are structurally disjoint from StepFun's, so a voice-keyed
    cache is already provider-safe. Callers pass the config that *actually*
    synthesizes each chunk (``client.active_config``), so after a fallback the
    remaining chunks are keyed on the fallback voice. Use ``--no-resume`` to force
    a full regeneration. Changing the voice or the source text (including via
    ``--max-chars``) does change the fingerprint, so stale audio is never reused.
    """

    h = hashlib.sha1()
    for part in (config.voice, config.response_format):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    h.update(text.encode("utf-8"))
    return h.hexdigest()[:12]


def _chunk_cache_path(cache_dir: Path, chunk: Chunk, config: TTSConfig) -> Path:
    fp = _chunk_fingerprint(config, chunk.text)
    return cache_dir / f"chunk_{chunk.index:05d}_{fp}.mp3"


def _rmdir_if_empty(directory: Path) -> None:
    """Remove ``directory`` only if it exists and is empty; otherwise leave it.

    A non-empty directory (a cache shared with another book/voice) or one that is
    already gone is a normal, expected outcome here — not an error to surface.
    """

    try:
        directory.rmdir()  # succeeds only on an empty dir — exactly our guard
    except OSError:
        pass  # not empty, or already removed — leave it in place


def _cleanup_cache(
    cache_dir: Path, chunk_files: dict[int, Path], console: Console
) -> None:
    """Delete this run's resume cache after a fully successful pipeline.

    Scoped deliberately narrowly. It removes ONLY the chunk files this run
    actually used or wrote — the values of ``chunk_files``, which are the exact
    fingerprinted paths taken from ``client.active_config`` at dispatch, so
    post-fallback chunks are keyed on the fallback voice (never recomputed from
    the primary config). It then removes the cache directory and its
    ``.audiobook_cache`` parent, but *only while each is empty*, so a cache dir
    shared with another book or voice keeps that other content. It never removes
    a directory tree wholesale.

    Cleanup failures never abort the run: the audiobook is already produced, so a
    leftover cache is merely wasted disk. A permission (or other OS) error while
    unlinking is reported as a note and swallowed.
    """

    try:
        for path in set(chunk_files.values()):
            path.unlink(missing_ok=True)
        _rmdir_if_empty(cache_dir)
        parent = cache_dir.parent
        if parent.name == _CACHE_DIRNAME:
            _rmdir_if_empty(parent)
    except OSError as exc:
        console.print(
            f"[yellow]Note:[/yellow] could not remove the resume cache under "
            f"{cache_dir} ({exc}). The audiobook is complete; delete it by hand "
            "to reclaim the disk."
        )


# Minimum plausible size for a real chunk MP3. StepFun chunks are hundreds of KB;
# anything this small is empty/garbage, not audio.
_MIN_CACHE_BYTES = 256


def _is_playable_mp3(path: Path) -> bool:
    """Return True if ``path`` looks like a complete MP3 worth reusing.

    We check the file is non-trivial in size and begins with an MP3 magic marker
    (an ID3v2 tag, or a bare MPEG audio frame sync). We deliberately do NOT use a
    decoder or mutagen's length estimate: StepFun's MP3s carry an ID3v2 header
    and no Xing/Info frame, so ``mutagen`` reports ``length == 0.0`` for
    perfectly valid audio — which previously made resume reject every cached
    chunk and regenerate the whole book. Atomic writes already guarantee a
    present cache file is fully written, so this magic-byte + size gate is the
    right validity check: it rejects empty/garbage files without false negatives
    on real audio.
    """

    try:
        if path.stat().st_size < _MIN_CACHE_BYTES:
            return False
        with open(path, "rb") as fh:
            head = fh.read(3)
    except OSError:
        return False
    if head[:3] == b"ID3":  # ID3v2-tagged MP3 (StepFun's output format)
        return True
    # Bare MPEG audio frame sync: 0xFF followed by 0b111xxxxx.
    return len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0


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
    client: FallbackTTSClient,
    cache_dir: Path,
    *,
    resume: bool,
    concurrency: int = DEFAULT_CONCURRENCY,
    rpm: int = DEFAULT_RPM,
    console: Console,
) -> dict[int, Path]:
    """Synthesize every chunk, returning a map of chunk index -> file path.

    Resume: chunks whose fingerprinted cache file already exists and is a valid
    MP3 are reused (not re-synthesized, not re-billed).

    Each chunk's cache path is fingerprinted from ``client.active_config`` at
    dispatch time, so if the run switches to the fallback provider mid-book the
    remaining chunks are keyed on the fallback voice (finding-4 fix). Accepted
    edge: the one chunk *during* which the switch happens is stored under the
    pre-switch fingerprint.

    Concurrency: ``concurrency`` requests run at once (``1`` = strictly
    sequential, the safe default), throttled to ``rpm`` request-starts per
    minute. Concurrency changes wall-clock time only — total characters (and
    therefore cost) are unchanged. The number in flight never exceeds
    ``concurrency`` and starts never exceed ``rpm`` (enforced by
    ``test_synthesis_concurrency_is_bounded``).
    """

    chunk_files: dict[int, Path] = {}
    todo: list[Chunk] = []
    for chunk in chunks:
        out_path = _chunk_cache_path(cache_dir, chunk, client.active_config)
        if resume and out_path.exists() and _is_playable_mp3(out_path):
            chunk_files[chunk.index] = out_path  # reuse cached audio
        else:
            todo.append(chunk)

    already_done = len(chunks) - len(todo)
    with _make_progress(console) as progress:
        task = progress.add_task("Synthesizing", total=len(chunks))
        if already_done:
            progress.advance(task, advance=already_done)
        if not todo:
            return chunk_files

        limiter = _RateLimiter(rpm)
        if concurrency <= 1:
            for chunk in todo:
                limiter.acquire()
                index, out_path = _synthesize_one(client, chunk, cache_dir, resume)
                chunk_files[index] = out_path
                progress.advance(task)
        else:
            _synthesize_concurrent(
                todo, chunk_files, client, cache_dir, limiter, progress, task,
                resume=resume, concurrency=concurrency,
            )

    return chunk_files


def _synthesize_one(
    client: FallbackTTSClient, chunk: Chunk, cache_dir: Path, resume: bool
) -> tuple[int, Path]:
    """Synthesize (or cache-hit) one chunk, keyed by the client's CURRENT config.

    The active config is read at dispatch, so after a fallback the path reflects
    the fallback voice — and a chunk already cached under that (switched) config
    from an earlier run is reused instead of re-billed.
    """

    out_path = _chunk_cache_path(cache_dir, chunk, client.active_config)
    if resume and out_path.exists() and _is_playable_mp3(out_path):
        return chunk.index, out_path
    client.synthesize_chunk(chunk, out_path)
    return chunk.index, out_path


def _synthesize_concurrent(
    todo: list[Chunk],
    chunk_files: dict[int, Path],
    client: FallbackTTSClient,
    cache_dir: Path,
    limiter: _RateLimiter,
    progress: Progress,
    task,
    *,
    resume: bool,
    concurrency: int,
) -> None:
    """Run the pending chunks through a bounded thread pool + RPM throttle.

    Each worker blocks on the shared rate limiter before its request, so no more
    than ``concurrency`` run at once and no more than the limiter's cap start per
    minute. On the first failure (or Ctrl-C) pending work is cancelled; requests
    already in flight are allowed to finish so their audio is cached for resume.
    """

    def work(chunk: Chunk) -> tuple[int, Path]:
        limiter.acquire()
        return _synthesize_one(client, chunk, cache_dir, resume)

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(work, c): c for c in todo}
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
