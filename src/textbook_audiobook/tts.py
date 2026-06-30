"""StepFun TTS client.

Uses the OpenAI Python SDK pointed at StepFun's OpenAI-compatible base URL
(``https://api.stepfun.ai/v1``). Calls ``POST /v1/audio/speech`` once per chunk
and writes the complete MP3 response to disk. No streaming — each call returns a
full audio file (see PLAN.md "Response Behaviour").

Handles transient failures with exponential backoff, honouring ``Retry-After``
on rate-limit (429) responses.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from textbook_audiobook.config import HARD_CHAR_LIMIT, StepFunConfig
from textbook_audiobook.models import Chunk


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
class StepFunTTSClient:
    config: StepFunConfig
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

    def __post_init__(self) -> None:
        self._client = self._build_client()
        self._active_model = self.config.model

    @property
    def active_model(self) -> str:
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
                f"StepFun's hard limit of {HARD_CHAR_LIMIT}."
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

        try:
            return self._attempt_with_retries(text, out_path, self._active_model)
        except _FatalError as exc:
            can_fallback = (
                exc.fallback_eligible
                and self.fallback_model is not None
                and self.fallback_model != self._active_model
            )
            if not can_fallback:
                self.stats.failures += 1
                raise TTSError(self._explain(exc)) from (exc.__cause__ or exc)

        # Fall back to the economy model and retry the whole document from here.
        self.stats.fallbacks += 1
        self._active_model = self.fallback_model  # type: ignore[assignment]
        try:
            return self._attempt_with_retries(text, out_path, self._active_model)
        except _FatalError as exc:
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
                out_path.write_bytes(audio_bytes)
                self.stats.requests += 1
                self.stats.characters += len(text)
                return out_path
            except _RetryableError as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
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

        self.stats.failures += 1
        plural = "s" if attempts != 1 else ""
        raise TTSError(
            f"Failed to synthesize with model '{model}' after {attempts} "
            f"attempt{plural}: {last_exc}"
        ) from last_exc

    @staticmethod
    def _explain(exc: "_FatalError") -> str:
        """Turn a fatal error into an actionable message."""

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
                "Use a valid StepFun voice ID (run `list-voices`); the default "
                "is 'cixingnansheng'. Note: OpenAI names like 'alloy' are not "
                "valid StepFun voices."
            )
        if exc.category == "model":
            return (
                f"StepFun rejected the model: {exc} "
                "Verify the --model name (e.g. stepaudio-2.5-tts or step-tts-2)."
            )
        return f"StepFun request failed: {exc}"

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
            raise TTSError("StepFun returned an empty audio payload.")
        return content

    # -- convenience ------------------------------------------------------

    def probe_voices(self) -> list[str] | None:
        """Best-effort attempt to list voices from the API.

        StepFun's OpenAI-compatible surface does not currently expose a voices
        endpoint in a stable way, so this returns ``None`` when unavailable and
        callers fall back to the static catalogue.
        """

        return None


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
