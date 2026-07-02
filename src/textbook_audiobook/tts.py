"""TTS client for OpenAI-compatible providers.

Uses the OpenAI Python SDK pointed at the selected provider's base URL (StepFun,
OpenRouter, …). Calls ``POST /v1/audio/speech`` once per chunk and writes the
complete MP3 response to disk. No streaming — each call returns a full audio
file (see PLAN.md "Response Behaviour").

The transport is provider-agnostic: every supported provider is
OpenAI-SDK-compatible, so this one client drives all of them. Provider-specific
behaviour is delegated to the :class:`~.providers.base.Provider` carried by the
:class:`~.config.TTSConfig` — the per-request character cap
(``config.hard_char_limit``) and the wording of error advice
(``provider.explain``).

Handles transient failures with exponential backoff, honouring ``Retry-After``
on rate-limit (429) responses.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from textbook_audiobook.config import TTSConfig
from textbook_audiobook.models import Chunk
from textbook_audiobook.providers.base import (
    is_quota_error,
    is_voice_error,
    retry_after,
)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically.

    Writes to a unique temp file in the same directory, flushes+fsyncs, then
    ``os.replace``s it into place (an atomic rename on the same filesystem).
    Guarantees the destination file is either absent or complete — never
    partially written — so an interrupt (Ctrl-C/crash/power loss) mid-write
    cannot leave a corrupt cache file that resume would later trust. Safe under
    concurrency: each call uses a distinct temp name (pid + thread id).
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.part")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        # If os.replace succeeded the temp is gone; this only cleans up when the
        # write or replace failed partway.
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


class TTSError(RuntimeError):
    """Raised when synthesis fails irrecoverably."""


@dataclass
class SynthesisStats:
    """Cumulative usage tracking for cost estimation and reporting."""

    requests: int = 0
    characters: int = 0
    retries: int = 0
    failures: int = 0
    fallbacks: int = 0


@dataclass
class TTSClient:
    config: TTSConfig
    max_retries: int = 5
    base_backoff: float = 1.0
    max_backoff: float = 60.0
    # If the primary model fails with an entitlement/model error, automatically
    # retry the whole document with this model. Set to None to disable.
    fallback_model: str | None = None
    stats: SynthesisStats = field(default_factory=SynthesisStats)
    _client: object | None = field(default=None, init=False, repr=False)
    # The model actually in use; switches to fallback_model after a fallback.
    _active_model: str = field(default="", init=False, repr=False)
    # Guards mutable shared state (stats + _active_model) so a single client can
    # be driven from several worker threads concurrently (see pipeline
    # --concurrency). The network call itself is made through the OpenAI client,
    # which is safe for concurrent use.
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = self._build_client()
        self._active_model = self.config.model

    @property
    def active_model(self) -> str:
        with self._lock:
            return self._active_model or self.config.model

    def _build_client(self):
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise TTSError(
                "The 'openai' package is required. Install with `uv add openai`."
            ) from exc
        return OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)

    # -- core synthesis ---------------------------------------------------

    def synthesize_chunk(self, chunk: Chunk, out_path: Path) -> Path:
        """Synthesize a single chunk to ``out_path``. Returns the written path."""

        limit = self.config.hard_char_limit
        if chunk.char_count > limit:
            raise TTSError(
                f"Chunk {chunk.index} has {chunk.char_count} chars, exceeding "
                f"{self.config.provider.label}'s hard limit of {limit}."
            )
        return self.synthesize_text(chunk.text, out_path)

    def synthesize_text(self, text: str, out_path: Path) -> Path:
        """Synthesize raw ``text`` to ``out_path``.

        Retries transient failures with backoff. On a permanent
        entitlement/model error with the primary model, automatically falls
        back to ``fallback_model`` (if configured) and retries once more.
        """

        if not text.strip():
            raise TTSError("Refusing to synthesize empty text.")

        out_path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            active_model = self._active_model

        try:
            return self._attempt_with_retries(text, out_path, active_model)
        except _FatalError as exc:
            can_fallback = (
                exc.fallback_eligible
                and self.fallback_model is not None
                and self.fallback_model != active_model
            )
            if not can_fallback:
                with self._lock:
                    self.stats.failures += 1
                raise TTSError(self._explain(exc)) from (exc.__cause__ or exc)

        # Fall back to the configured fallback model and retry the whole document
        # from here. Under concurrency, several threads may hit the dead primary
        # at once; flipping to the same fallback model repeatedly is idempotent.
        with self._lock:
            self.stats.fallbacks += 1
            self._active_model = self.fallback_model  # type: ignore[assignment]
            active_model = self._active_model
        try:
            return self._attempt_with_retries(text, out_path, active_model)
        except _FatalError as exc:
            with self._lock:
                self.stats.failures += 1
            raise TTSError(self._explain(exc)) from (exc.__cause__ or exc)

    def _attempt_with_retries(
        self, text: str, out_path: Path, model: str
    ) -> Path:
        """Run one model through the retry/backoff loop. Raises on failure."""

        last_exc: Exception | None = None
        attempts = 0

        for attempt in range(self.max_retries + 1):
            attempts = attempt + 1
            try:
                audio_bytes = self._request_audio(text, model)
                # Atomic write: the cache file only appears once fully written,
                # so an interrupt can never leave a partial file that resume
                # would wrongly trust.
                _atomic_write_bytes(out_path, audio_bytes)
                with self._lock:
                    self.stats.requests += 1
                    self.stats.characters += len(text)
                return out_path
            except _RetryableError as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                with self._lock:
                    self.stats.retries += 1
                delay = exc.retry_after if exc.retry_after is not None else (
                    min(self.base_backoff * (2**attempt), self.max_backoff)
                )
                time.sleep(delay)
            except _FatalError:
                # Permanent — propagate to synthesize_text for fallback handling.
                raise
            except Exception as exc:  # unexpected, non-retryable
                last_exc = exc
                break

        with self._lock:
            self.stats.failures += 1
        plural = "s" if attempts != 1 else ""
        raise TTSError(
            f"Failed to synthesize with model '{model}' after {attempts} "
            f"attempt{plural}: {last_exc}"
        ) from last_exc

    def _explain(self, exc: "_FatalError") -> str:
        """Turn a fatal error into an actionable, provider-specific message."""

        return self.config.provider.explain(exc.category, exc)

    def _request_audio(self, text: str, model: str) -> bytes:
        """Make one API call and return the complete audio payload as bytes."""

        # Import lazily so error types are available without a hard import at
        # module load time.
        from openai import (  # type: ignore
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            InternalServerError,
            NotFoundError,
            PermissionDeniedError,
            RateLimitError,
        )

        assert self._client is not None
        try:
            response = self._client.audio.speech.create(  # type: ignore[attr-defined]
                model=model,
                voice=self.config.voice,
                input=text,
                response_format=self.config.response_format,
            )
        except RateLimitError as exc:
            raise _RetryableError(str(exc), retry_after=retry_after(exc)) from exc
        except (APITimeoutError, APIConnectionError, InternalServerError) as exc:
            raise _RetryableError(str(exc)) from exc
        except AuthenticationError as exc:
            # Wrong/missing key — a model fallback cannot fix this.
            raise _FatalError(str(exc), category="auth", fallback_eligible=False) from exc
        except (NotFoundError, BadRequestError) as exc:
            if is_voice_error(exc):
                # Bad voice ID — swapping the model can't fix it.
                raise _FatalError(
                    str(exc), category="voice", fallback_eligible=False
                ) from exc
            # Unknown/invalid model or request — a fallback model may work.
            raise _FatalError(str(exc), category="model", fallback_eligible=True) from exc
        except (PermissionDeniedError, APIStatusError) as exc:
            # Quota (402), entitlement (403), and other status errors. These may
            # be model-specific, so a fallback to another model is worth a try.
            category = "quota" if is_quota_error(exc) else "model"
            raise _FatalError(str(exc), category=category, fallback_eligible=True) from exc

        # The binary response exposes the full payload; no streaming assembly.
        content = getattr(response, "content", None)
        if content is None and hasattr(response, "read"):
            content = response.read()
        if not content:
            raise TTSError(
                f"{self.config.provider.label} returned an empty audio payload."
            )
        return content


class _RetryableError(Exception):
    """Internal marker for transient errors worth retrying."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class _FatalError(Exception):
    """Internal marker for permanent errors that retrying cannot fix.

    ``fallback_eligible`` signals whether switching to the fallback model might
    succeed (e.g. quota/entitlement scoped to a specific model) versus errors a
    model swap can never resolve (e.g. a bad API key).
    """

    def __init__(
        self, message: str, *, category: str, fallback_eligible: bool
    ) -> None:
        super().__init__(message)
        self.category = category
        self.fallback_eligible = fallback_eligible
