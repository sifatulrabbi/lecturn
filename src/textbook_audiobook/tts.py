"""TTS clients for the supported OpenAI-compatible providers.

StepFun, OpenRouter, and the self-hosted ``local`` Kokoro server all expose the
same ``POST /audio/speech`` shape, so the transport, retry/backoff,
model-fallback, atomic-write, and error-mapping logic is shared in
:class:`_BaseTTSClient`. Each provider is a thin subclass that supplies
human-readable error guidance via ``_explain`` (and, for the Kokoro-based
providers, a token-input guard) — everything else is identical. Uses the OpenAI
Python SDK pointed at the provider's base URL, calls the endpoint once per
chunk, and writes the complete MP3 response to disk (no streaming — each call
returns a full audio file, see PLAN.md "Response Behaviour").

Transient failures are retried with exponential backoff, honouring
``Retry-After`` on rate-limit (429) responses.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, ClassVar

from textbook_audiobook.config import (
    HARD_CHAR_LIMIT,
    LOCAL_MODELS,
    OPENROUTER_MODELS,
    MissingApiKeyError,
    TTSConfig,
)
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
    """Raised when synthesis fails irrecoverably.

    ``fallback_eligible`` marks a failure that switching to a *different provider*
    might recover (quota/unknown-model) versus one it cannot (bad key/voice). The
    :class:`FallbackTTSClient` reads it to decide whether to switch providers.
    """

    def __init__(self, message: str, *, fallback_eligible: bool = False) -> None:
        super().__init__(message)
        self.fallback_eligible = fallback_eligible


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
    loop, ``Retry-After`` handling, atomic cache writes, OpenAI-SDK error mapping,
    and thread-safe stats. Providers subclass this and override :meth:`_explain`
    to give account-specific guidance; the config is duck-typed (only ``.api_key``
    / ``.base_url`` / ``.model`` / ``.voice`` / ``.response_format`` are used), so
    any :data:`~textbook_audiobook.config.TTSConfig` works. Not instantiated
    directly — use a provider subclass.

    A single client speaks exactly one provider/model/voice. Cross-provider
    fallback (StepFun → OpenRouter/Kokoro on a quota outage) lives one level up in
    :class:`FallbackTTSClient`, which owns a primary and a lazily-built fallback
    client; this class no longer swaps models internally.
    """

    # Human-readable provider name used in error messages; subclasses override.
    provider_label: ClassVar[str] = "The TTS provider"

    config: TTSConfig
    max_retries: int = 5
    base_backoff: float = 1.0
    max_backoff: float = 60.0
    stats: SynthesisStats = field(default_factory=SynthesisStats)
    _client: object | None = field(default=None, init=False, repr=False)
    # Guards mutable shared state (stats) so a single client can be driven from
    # several worker threads concurrently (see pipeline --concurrency). The
    # network call itself is made through the OpenAI client, which is safe for
    # concurrent use. FallbackTTSClient reassigns this to a shared lock so a
    # primary and fallback client serialise their stats updates during a switch.
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = self._build_client()

    @property
    def active_model(self) -> str:
        """The model this client synthesizes with (its config's model)."""

        return self.config.model

    @property
    def active_config(self) -> TTSConfig:
        """The config this client synthesizes with (used for cache fingerprints)."""

        return self.config

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

        Retries transient failures with backoff. A permanent error raises a
        :class:`TTSError` carrying ``fallback_eligible`` so a wrapping
        :class:`FallbackTTSClient` can decide whether a provider switch is worth
        trying — this client never swaps model or provider itself.
        """

        if not text.strip():
            raise TTSError("Refusing to synthesize empty text.")

        out_path.parent.mkdir(parents=True, exist_ok=True)

        model = self.config.model

        # Reject a chunk that would overrun the model's input token limit before
        # spending a call. No-op for providers without a token cap.
        self._check_input_limits(text, model)

        try:
            return self._attempt_with_retries(text, out_path, model)
        except _FatalError as exc:
            with self._lock:
                self.stats.failures += 1
            raise TTSError(
                self._explain(exc), fallback_eligible=exc.fallback_eligible
            ) from (exc.__cause__ or exc)

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
        """Turn a fatal error into an actionable message.

        Provider hook: the generic default is deliberately terse. Each provider
        subclass overrides this with account-specific guidance (quota top-up
        links, which env var to check, how to list voices). Instance method so
        overrides can reach per-instance state such as the configured base URL.
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
            # be model/provider-specific, so a provider fallback is worth a try.
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

    Only the provider-specific error guidance lives here; the transport is the
    shared base. Cross-provider fallback (to OpenRouter/Kokoro on a quota outage)
    is orchestrated by :class:`FallbackTTSClient`, not this client.
    """

    provider_label: ClassVar[str] = "StepFun"

    def _explain(self, exc: "_FatalError") -> str:
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

    Same transport as StepFun; only the error guidance and the token-limit guard
    differ. OpenRouter is also the target every :class:`FallbackTTSClient` falls
    back to, so this client doubles as the fallback synthesizer.
    """

    provider_label: ClassVar[str] = "OpenRouter"

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

    def _explain(self, exc: "_FatalError") -> str:
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


@dataclass
class LocalTTSClient(_BaseTTSClient):
    """TTS client for a self-hosted Kokoro server (default ``127.0.0.1:8880``).

    Structurally a clone of :class:`OpenRouterTTSClient` (same transport, same
    Kokoro model family, same 4096-token input guard) rather than a subclass of
    it — the token hook is cloned explicitly so the two providers stay
    independent. Only the error guidance differs: it is tailored to a
    self-hosted server and names the configured base URL.
    """

    provider_label: ClassVar[str] = "Local"

    def _check_input_limits(self, text: str, model: str) -> None:
        """Reject a chunk whose token count would overrun the model's cap.

        Same guard as :class:`OpenRouterTTSClient`, driven by ``LOCAL_MODELS``
        (Kokoro's 4096-token ceiling). Cloned rather than inherited so the local
        provider does not depend on the OpenRouter class. NOTE: with
        ``HARD_CHAR_LIMIT`` (1000) a chunk stays far under the cap, so this can
        never fire today — it makes the constraint explicit and future-proofs
        against a raised char cap or a lower-limit model.
        """

        info = LOCAL_MODELS.get(model)
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

    def _explain(self, exc: "_FatalError") -> str:
        """Turn a fatal error into actionable, local-server-specific guidance.

        The messages name the configured base URL — the single most useful thing
        to check when a self-hosted server misbehaves. Note that a *dead* server
        surfaces as a connection error, which is retryable and therefore reported
        by the retry loop's "after N attempts" message, not here; the catch-all
        branch still points at the server for any other/uncategorised failure.
        """

        base_url = self.config.base_url
        if exc.category == "quota":
            # A self-hosted server should not meter quota; a proxy in front of it
            # might. Surface the raw error and point at the server.
            return (
                f"The local TTS server rejected the request: {exc} "
                f"That is unusual for a self-hosted server at {base_url} — check "
                "its logs (see the server/ README)."
            )
        if exc.category == "auth":
            return (
                f"The local TTS server rejected the credentials: {exc} "
                "Local servers are usually unauthenticated; if yours needs a key, "
                "set LOCAL_TTS_API_KEY."
            )
        if exc.category == "voice":
            return (
                f"The local TTS server rejected the voice: {exc} "
                "Run `lecturn list-voices --provider local` and pick a valid "
                "Kokoro voice ID (e.g. 'af_heart')."
            )
        if exc.category == "model":
            return (
                f"The local TTS server rejected the model: {exc} "
                f"Check the --model name (default 'kokoro') is one the server at "
                f"{base_url} serves (see the server/ README)."
            )
        return (
            f"The local TTS request failed: {exc} "
            f"Is the local TTS server running at {base_url}? "
            "(see the server/ README)"
        )


@dataclass
class FallbackTTSClient:
    """Drives a primary TTS client, switching once to an OpenRouter fallback.

    Wraps a primary :class:`_BaseTTSClient`. On the first *fallback-eligible*
    failure it lazily builds the fallback client (always OpenRouter/Kokoro) via
    ``fallback_factory`` and routes every subsequent chunk through it —
    **one-way, for the rest of the run**. It exposes the surface the pipeline
    drives (:meth:`synthesize_chunk`, :attr:`stats`, :attr:`active_model`,
    :attr:`active_config`), so it is a drop-in for a bare client.

    Thread-safety: the primary and fallback clients share this wrapper's lock and
    ``stats``, so their counter updates never race during the switch window (an
    in-flight primary request finishing while a fallback request runs). The
    switch itself happens exactly once even when several workers hit the dead
    primary simultaneously — the losers observe the already-active fallback and
    retry their own chunk on it.

    ``fallback_factory`` is called lazily, so a run whose primary never fails
    never needs the fallback provider's credentials. If it raises
    :class:`~textbook_audiobook.config.MissingApiKeyError` when the fallback is
    finally needed, the original primary error is surfaced with a note that the
    fallback was skipped. Pass ``fallback_factory=None`` to disable fallback.
    """

    primary: _BaseTTSClient
    fallback_factory: Callable[[], _BaseTTSClient] | None = None
    stats: SynthesisStats = field(default_factory=SynthesisStats)
    _active: _BaseTTSClient = field(init=False, repr=False)
    _switched: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        # Share the wrapper's lock and stats with the primary so both are already
        # aggregated and serialised before any fallback is built.
        self.primary._lock = self._lock
        self.primary.stats = self.stats
        self._active = self.primary

    @property
    def active_model(self) -> str:
        with self._lock:
            return self._active.active_model

    @property
    def active_config(self) -> TTSConfig:
        with self._lock:
            return self._active.config

    def synthesize_chunk(self, chunk: Chunk, out_path: Path) -> Path:
        return self._run(lambda client: client.synthesize_chunk(chunk, out_path))

    def synthesize_text(self, text: str, out_path: Path) -> Path:
        return self._run(lambda client: client.synthesize_text(text, out_path))

    def _run(self, action: Callable[[_BaseTTSClient], Path]) -> Path:
        with self._lock:
            active = self._active
            already_switched = self._switched
        try:
            return action(active)
        except TTSError as exc:
            if (
                already_switched
                or self.fallback_factory is None
                or not getattr(exc, "fallback_eligible", False)
            ):
                raise
            fallback = self._switch(exc)
            # Retry this chunk on the fallback. If it also fails, that error
            # propagates — the switch is one-way, we never loop.
            return action(fallback)

    def _switch(self, original: TTSError) -> _BaseTTSClient:
        """Build (once) and activate the fallback client. Thread-safe."""

        with self._lock:
            if self._switched:
                return self._active  # another worker already switched
            assert self.fallback_factory is not None
            try:
                fallback = self.fallback_factory()
            except MissingApiKeyError as exc:
                raise TTSError(
                    f"{original} Automatic fallback to OpenRouter (Kokoro) was "
                    "skipped because OPENROUTER_API_KEY is not set."
                ) from exc
            # Share this wrapper's lock and stats so the fallback's counters
            # aggregate and serialise with the primary's during the switch.
            fallback._lock = self._lock
            fallback.stats = self.stats
            self._active = fallback
            self._switched = True
            self.stats.fallbacks += 1
            return fallback


class _RetryableError(Exception):
    """Internal marker for transient errors worth retrying."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class _FatalError(Exception):
    """Internal marker for permanent errors that retrying cannot fix.

    ``fallback_eligible`` signals whether switching to a different provider might
    succeed (e.g. quota/entitlement scoped to a model) versus errors a provider
    swap can never resolve (e.g. a bad API key or a rejected voice).
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
