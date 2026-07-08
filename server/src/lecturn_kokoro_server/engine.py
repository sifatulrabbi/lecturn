"""Kokoro synthesis engine — a thin, testable wrapper around ``KPipeline``.

Responsibilities:

* **Device selection** — CUDA > MPS > CPU, detected lazily (so importing this
  module never imports torch; tests inject a device string instead).
* **Per-language pipelines** — a ``KPipeline`` is bound to one ``lang_code``
  (the first letter of the voice ID). We build one lazily per language and cache
  it behind a lock; the model itself is shared across a language's voices.
* **Synthesis** — run the pipeline, concatenate *all* yielded segment audio in
  order, and return a single float32 array. Synthesis is serialized with a lock:
  lecturn's default concurrency is low and correctness beats throughput here.

The ``KPipeline`` factory is injectable (``pipeline_factory``) so tests can
supply a fake that returns canned numpy audio — no weights, no torch, no GPU.
The real factory imports ``kokoro`` lazily, so nothing heavy is imported until
the first synthesis (or an explicit :meth:`warm`).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable

import numpy as np

logger = logging.getLogger(__name__)

# hexgrad's canonical weights repo; passed to KPipeline to silence its
# "defaulting repo_id" warning and make the source explicit.
REPO_ID: str = "hexgrad/Kokoro-82M"

# A factory takes (lang_code, device) and returns a callable pipeline. The
# pipeline is invoked as ``pipeline(text, voice=..., speed=...)`` and yields
# segments (see _extract_audio for the accepted shapes).
PipelineFactory = Callable[[str, str], Callable[..., Iterable[object]]]


def lang_code_for(voice: str) -> str:
    """Return the KPipeline ``lang_code`` for a voice ID (its first letter)."""

    if not voice:
        raise ValueError("voice must be a non-empty string")
    return voice[0]


def detect_device() -> str:
    """Pick the best available torch device: CUDA > MPS > CPU."""

    import torch  # lazy: importing this module must not import torch

    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def _default_pipeline_factory(lang_code: str, device: str):
    """Build a real ``KPipeline`` for ``lang_code`` on ``device``.

    Imported lazily: the first call for any language triggers a one-time weight
    download (~327 MB) from the HF Hub, which can take minutes on a cold cache.
    """

    from kokoro import KPipeline  # lazy: heavy torch import happens here

    logger.info(
        "Loading Kokoro pipeline (lang_code=%r, device=%s) from %s. "
        "First run downloads ~327 MB of weights from Hugging Face; "
        "subsequent runs use the local cache.",
        lang_code,
        device,
        REPO_ID,
    )
    return KPipeline(lang_code=lang_code, repo_id=REPO_ID, device=device)


def _extract_audio(segment: object) -> np.ndarray | None:
    """Pull the float32 audio array out of one pipeline-yielded segment.

    kokoro's output shape has drifted across releases, so accept both the
    ``Result`` object (``.audio``) and the ``(graphemes, phonemes, audio)``
    tuple, and tolerate torch tensors or numpy arrays. Returns ``None`` for a
    segment with no audio (e.g. a pause), which the caller skips.
    """

    audio = getattr(segment, "audio", None)
    if audio is None and isinstance(segment, (tuple, list)) and segment:
        audio = segment[-1]
    if audio is None:
        return None
    # torch.Tensor -> numpy without importing torch here.
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    return np.asarray(audio, dtype=np.float32).reshape(-1)


class KokoroEngine:
    """Loads Kokoro pipelines on demand and synthesizes voice audio."""

    def __init__(
        self,
        *,
        pipeline_factory: PipelineFactory | None = None,
        device: str | None = None,
    ) -> None:
        self._factory: PipelineFactory = pipeline_factory or _default_pipeline_factory
        self._device = device
        self._pipelines: dict[str, Callable[..., Iterable[object]]] = {}
        self._pipelines_lock = threading.Lock()
        self._synth_lock = threading.Lock()

    @property
    def device(self) -> str:
        """The torch device in use, detected (and cached) lazily on first read."""

        if self._device is None:
            self._device = detect_device()
        return self._device

    def _pipeline_for(self, lang_code: str) -> Callable[..., Iterable[object]]:
        """Return the cached pipeline for ``lang_code``, building it if needed."""

        with self._pipelines_lock:
            pipeline = self._pipelines.get(lang_code)
            if pipeline is None:
                pipeline = self._factory(lang_code, self.device)
                self._pipelines[lang_code] = pipeline
            return pipeline

    def warm(self, voice: str = "af_heart") -> None:
        """Eagerly build the pipeline for ``voice`` (triggers the weight download).

        Optional startup convenience so the first real request isn't slow. Any
        failure is logged and swallowed — warming must never crash the server.
        """

        try:
            self._pipeline_for(lang_code_for(voice))
            logger.info("Kokoro pipeline warmed for voice %r.", voice)
        except Exception:  # pragma: no cover - best-effort startup path
            logger.exception("Failed to warm Kokoro pipeline for voice %r.", voice)

    def synthesize(
        self, text: str, voice: str, *, speed: float = 1.0
    ) -> np.ndarray:
        """Synthesize ``text`` with ``voice`` into one float32 PCM array (24 kHz).

        Concatenates every segment the pipeline yields, in order. Raises
        ``RuntimeError`` if the pipeline produces no audio at all.
        """

        pipeline = self._pipeline_for(lang_code_for(voice))
        chunks: list[np.ndarray] = []
        # Serialize synthesis: a single KPipeline is not guaranteed re-entrant,
        # and lecturn drives us at low concurrency, so correctness wins.
        with self._synth_lock:
            for segment in pipeline(text, voice=voice, speed=speed):
                audio = _extract_audio(segment)
                if audio is not None and audio.size:
                    chunks.append(audio)
        if not chunks:
            raise RuntimeError(
                f"Kokoro produced no audio for voice {voice!r} "
                "(input may be empty after normalization)."
            )
        return np.concatenate(chunks)
