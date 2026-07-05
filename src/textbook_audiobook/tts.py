"""TTS clients for the supported OpenAI-compatible providers.

Both StepFun and OpenRouter expose the same ``POST /audio/speech`` shape, so the
transport, retry/backoff, model-fallback, atomic-write, and error-mapping logic
is shared in :class:`_BaseTTSClient`. Each provider is a thin subclass that only
supplies human-readable error guidance via ``_explain`` — everything else is
identical. Uses the OpenAI Python SDK pointed at the provider's base URL, calls
the endpoint once per chunk, and writes the complete MP3 response to disk (no
streaming — each call returns a full audio file, see PLAN.md "Response
Behaviour").

Transient failures are retried with exponential backoff, honouring
``Retry-After`` on rate-limit (429) responses.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from textbook_audiobook.config import HARD_CHAR_LIMIT, OPENROUTER_MODELS, TTSConfig
from textbook_audiobook.models import Chunk
from textbook_audiobook.tokens import count_tokens

# Fraction of a model's token ceiling we allow through, leaving headroom for the
# fact that we approximate token counts (no public Kokoro tokenizer). For
# Kokoro's 4096-token cap this rejects above ~3891 tokens.
_TOKEN_LIMIT_SAFETY: float = 0.95


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
class _BaseTTSClient:
    """Provider-agnostic TTS client.

    Holds all the transport machinery shared by every provider: the retry/backoff
    loop, ``Retry-After`` handling, automatic model fallback, atomic cache writes,
    OpenAI-SDK error mapping, and thread-safe stats. Providers subclass this and
    override :meth:`_explain` to give account-specific guidance; the config is
    duck-typed (only ``.api_key`` / ``.base_url`` / ``.model`` / ``.voice`` /
    ``.response_format`` are used), so any :data:`~textbook_audiobook.config.TTSConfig`
    works. Not instantiated directly — use a provider subclass.
    """

    # Human-readable provider name used in error messages; subclasses override.
    provider_label: ClassVar[str] = "The TTS provider"

    config: TTSConfig
    max_retries: int = 5
    base_backoff: float = 1.0
    max_backoff: float = 60.0
    # If the primary model fails with an entitlement/model error, automatically
    # retry the whole document with this economy model. Set to None to disable.
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

        if chunk.char_count > HARD_CHAR_LIMIT:
            raise TTSError(
                f"Chunk {chunk.index} has {chunk.char_count} chars, exceeding "
                f"the hard limit of {HARD_CHAR_LIMIT}."
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

        # Reject a chunk that would overrun the model's input token limit before
        # spending a call. No-op for providers without a token cap.
        self._check_input_limits(text, active_model)

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

        # Fall back to the economy model and retry the whole document from here.
        # Under concurrency, several threads may hit the dead primary at once;
        # flipping to the same fallback model repeatedly is idempotent.
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

    @staticmethod
    def _explain(exc: "_FatalError") -> str:
        """Turn a fatal error into an actionable message.

        Provider hook: the generic default is deliberately terse. Each provider
        subclass overrides this with account-specific guidance (quota top-up
        links, which env var to check, how to list voices).
        """

        return f"TTS request failed: {exc}"

    def _check_input_limits(self, text: str, model: str) -> None:
        """Validate ``text`` against ``model``'s input limits before a request.

        Provider hook: the base does nothing (StepFun imposes no token cap).
        Providers with a token ceiling (e.g. OpenRouter/Kokoro) override this to
        reject an over-limit chunk with a clear :class:`TTSError`.
        """

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
            raise _RetryableError(str(exc), retry_after=_retry_after(exc)) from exc
        except (APITimeoutError, APIConnectionError, InternalServerError) as exc:
            raise _RetryableError(str(exc)) from exc
        except AuthenticationError as exc:
            # Wrong/missing key — a model fallback cannot fix this.
            raise _FatalError(str(exc), category="auth", fallback_eligible=False) from exc
        except (NotFoundError, BadRequestError) as exc:
            if _is_voice_error(exc):
                # Bad voice ID — swapping the model can't fix it.
                raise _FatalError(
                    str(exc), category="voice", fallback_eligible=False
                ) from exc
            # Unknown/invalid model or request — a fallback model may work.
            raise _FatalError(str(exc), category="model", fallback_eligible=True) from exc
        except (PermissionDeniedError, APIStatusError) as exc:
            # Quota (402), entitlement (403), and other status errors. These may
            # be model-specific, so a fallback to the economy model is worth a try.
            category = "quota" if _is_quota_error(exc) else "model"
            raise _FatalError(str(exc), category=category, fallback_eligible=True) from exc

        # The binary response exposes the full payload; no streaming assembly.
        content = getattr(response, "content", None)
        if content is None and hasattr(response, "read"):
            content = response.read()
        if not content:
            raise TTSError(f"{self.provider_label} returned an empty audio payload.")
        return content

    # -- convenience ------------------------------------------------------

    def probe_voices(self) -> list[str] | None:
        """Best-effort attempt to list voices from the API.

        StepFun's OpenAI-compatible surface does not currently expose a voices
        endpoint in a stable way, so this returns ``None`` when unavailable and
        callers fall back to the static catalogue.
        """

        return None


@dataclass
class StepFunTTSClient(_BaseTTSClient):
    """TTS client for StepFun (``https://api.stepfun.ai/v1``).

    Behaviour is identical to the pre-refactor client; only the provider-specific
    error guidance lives here. Fallback to an economy model is a StepFun concept,
    so ``fallback_model`` stays available (default off).
    """

    provider_label: ClassVar[str] = "StepFun"

    @staticmethod
    def _explain(exc: "_FatalError") -> str:
        """Turn a fatal error into an actionable, StepFun-specific message."""

        if exc.category == "quota":
            return (
                f"StepFun rejected the request: {exc} "
                "Your account has no remaining quota/credit for this model. "
                "Add credit or switch plans at https://platform.stepfun.ai, or "
                "try the economy model with --model step-tts-2."
            )
        if exc.category == "auth":
            return (
                f"StepFun authentication failed: {exc} "
                "Check that STEPFUN_API_KEY is set to a valid key."
            )
        if exc.category == "voice":
            return (
                f"StepFun rejected the voice: {exc} "
                "This voice ID may not be enabled for your account (access is "
                "per-account). Run `list-voices` and try another — the "
                "English-keyed voices (e.g. 'lively-girl', 'elegantgentle-female') "
                "are the most widely available. Note: OpenAI names like 'alloy' "
                "are not valid StepFun voices."
            )
        if exc.category == "model":
            return (
                f"StepFun rejected the model: {exc} "
                "Verify the --model name (e.g. stepaudio-2.5-tts or step-tts-2)."
            )
        return f"StepFun request failed: {exc}"


@dataclass
class OpenRouterTTSClient(_BaseTTSClient):
    """TTS client for OpenRouter (``https://openrouter.ai/api/v1``, Kokoro-82M).

    Same transport as StepFun; only the error guidance differs. Model fallback is
    a StepFun-tier concept, so it defaults off here (``fallback_model=None``) —
    Kokoro is the single OpenRouter model — though a caller may still set one.
    """

    provider_label: ClassVar[str] = "OpenRouter"

    fallback_model: str | None = None

    def _check_input_limits(self, text: str, model: str) -> None:
        """Reject a chunk whose token count would overrun the model's cap.

        Kokoro accepts ``max_input_tokens`` (4096) per request. We count tokens
        with a tiktoken approximation (Kokoro publishes no tokenizer) and reject
        above a safety margin. NOTE: with ``HARD_CHAR_LIMIT`` (1000) a chunk is
        at most ~1000 chars ≈ 300 tokens, so this can never fire today — it makes
        the constraint explicit and future-proofs against a raised char cap or a
        lower-limit model.
        """

        info = OPENROUTER_MODELS.get(model)
        if info is None or info.max_input_tokens is None:
            return
        ceiling = int(info.max_input_tokens * _TOKEN_LIMIT_SAFETY)
        n_tokens = count_tokens(text)
        if n_tokens > ceiling:
            raise TTSError(
                f"Chunk is ~{n_tokens} tokens, over {model}'s safe input limit "
                f"of {ceiling} (model max {info.max_input_tokens}). "
                "Lower --max-chars to produce smaller chunks."
            )

    @staticmethod
    def _explain(exc: "_FatalError") -> str:
        """Turn a fatal error into an actionable, OpenRouter-specific message."""

        if exc.category == "quota":
            return (
                f"OpenRouter rejected the request: {exc} "
                "Your account is out of credits for this model. Add credits at "
                "https://openrouter.ai/settings/credits."
            )
        if exc.category == "auth":
            return (
                f"OpenRouter authentication failed: {exc} "
                "Check that OPENROUTER_API_KEY is set to a valid key."
            )
        if exc.category == "voice":
            return (
                f"OpenRouter rejected the voice: {exc} "
                "Run `lecturn list-voices --provider openrouter` and pick a valid "
                "Kokoro voice ID (e.g. 'af_heart')."
            )
        if exc.category == "model":
            return (
                f"OpenRouter rejected the model: {exc} "
                "Verify the --model name against "
                "`lecturn list-models --provider openrouter` "
                "(e.g. 'hexgrad/kokoro-82m')."
            )
        return f"OpenRouter request failed: {exc}"


class _RetryableError(Exception):
    """Internal marker for transient errors worth retrying."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class _FatalError(Exception):
    """Internal marker for permanent errors that retrying cannot fix.

    ``fallback_eligible`` signals whether switching to the economy model might
    succeed (e.g. quota/entitlement scoped to a specific model) versus errors a
    model swap can never resolve (e.g. a bad API key).
    """

    def __init__(
        self, message: str, *, category: str, fallback_eligible: bool
    ) -> None:
        super().__init__(message)
        self.category = category
        self.fallback_eligible = fallback_eligible


def _is_quota_error(exc: Exception) -> bool:
    """Heuristically detect a quota/billing rejection (commonly HTTP 402)."""

    status = getattr(exc, "status_code", None)
    if status == 402:
        return True
    text = str(exc).lower()
    return "quota" in text or "billing" in text or "insufficient" in text


def _is_voice_error(exc: Exception) -> bool:
    """Detect a rejected/invalid voice ID (StepFun type 'voice_id_invalid')."""

    text = str(exc).lower()
    return "voice_id" in text or "voice" in text


def _retry_after(exc: Exception) -> float | None:
    """Extract a Retry-After hint (seconds) from an OpenAI error, if present."""

    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
