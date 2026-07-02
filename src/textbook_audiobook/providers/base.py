"""Provider abstraction for TTS backends.

A ``Provider`` captures everything that differs between TTS services: the base
URL, the API-key environment variables, the model/voice catalogues (with
pricing), the per-request character cap, sensible concurrency/RPM guidance, and
the wording of actionable error advice.

The *transport* does not live here. Every provider we support is
OpenAI-SDK-compatible (``client.audio.speech.create(...)`` returning a full audio
body), so a single generic client in :mod:`textbook_audiobook.tts` drives all of
them. The provider only supplies configuration + catalogues + advice copy.

The OpenAI SDK exception taxonomy is shared across providers, so the error
*classification* heuristics (``is_quota_error`` / ``is_voice_error`` /
``retry_after``) are defined here as plain functions and reused everywhere. Only
the *advice* a user sees (``Provider.explain``) is provider-specific.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    """Metadata for a TTS model, including pricing for cost estimation."""

    name: str
    # USD per 10,000 characters. For providers that bill per token rather than
    # per character (e.g. OpenRouter) this is a best-effort approximation used
    # only for the pre-run estimate — never for billing.
    price_per_10k_chars: float
    description: str


class Provider(ABC):
    """A TTS backend: connection defaults, catalogues, and error advice.

    Concrete providers set the class attributes below and implement
    :meth:`explain`. One instance per provider is held in the registry
    (:mod:`textbook_audiobook.providers`).
    """

    #: Short machine name used on the CLI (``--provider <name>``) and as part of
    #: the resume-cache fingerprint.
    name: str
    #: Human-facing label for tables/messages, e.g. ``"StepFun"``.
    label: str
    #: Default OpenAI-compatible base URL (``.../v1``).
    default_base_url: str
    #: Environment variable that overrides the base URL, if set.
    base_url_env: str
    #: API-key environment variables, tried in order.
    api_key_env: tuple[str, ...]
    #: Default model when ``--model`` is not given.
    default_model: str
    #: Default voice when ``--voice`` is not given. Must be valid for
    #: ``default_model`` (voices can be model-specific — see OpenRouter).
    default_voice: str
    #: Model to auto-retry with on a fallback-eligible rejection, or ``None`` to
    #: disable automatic fallback by default.
    default_fallback_model: str | None
    #: Hard per-request character cap enforced by the service. Chunks never
    #: exceed this; ``--max-chars`` may only lower it.
    hard_char_limit: int
    #: Account per-model concurrency guidance (used for the CLI warning).
    concurrency_limit: int
    #: Account per-model requests-per-minute guidance (used as the ``--rpm``
    #: default).
    default_rpm: int
    #: Static model catalogue (id -> :class:`ModelInfo`).
    models: dict[str, ModelInfo]
    #: Static voice catalogue (id -> human description).
    voices: dict[str, str]

    # -- credentials ------------------------------------------------------

    def resolve_api_key(self) -> str | None:
        """Return the first API key found in :attr:`api_key_env`, or ``None``."""

        for var in self.api_key_env:
            value = os.environ.get(var)
            if value:
                return value
        return None

    def resolve_base_url(self, override: str | None = None) -> str:
        """Resolve the base URL: explicit override, then env, then default."""

        return override or os.environ.get(self.base_url_env) or self.default_base_url

    # -- pricing ----------------------------------------------------------

    def estimate_cost(self, char_count: int, model: str) -> float:
        """Estimate USD cost for narrating ``char_count`` characters.

        Unknown models estimate to 0.0 (the CLI already warns on unknown
        models). For per-token-billed providers this is approximate.
        """

        info = self.models.get(model)
        if info is None:
            return 0.0
        return (char_count / 10_000) * info.price_per_10k_chars

    # -- error advice -----------------------------------------------------

    @abstractmethod
    def explain(self, category: str, exc: Exception) -> str:
        """Return an actionable message for a fatal error ``category``.

        ``category`` is one of ``"quota"``, ``"auth"``, ``"voice"``,
        ``"model"``. Providers phrase advice with their own URLs, model names,
        and voice guidance.
        """


# -- shared error classification (OpenAI SDK taxonomy is provider-agnostic) ---


def is_quota_error(exc: Exception) -> bool:
    """Heuristically detect a quota/billing rejection (commonly HTTP 402)."""

    status = getattr(exc, "status_code", None)
    if status == 402:
        return True
    text = str(exc).lower()
    return "quota" in text or "billing" in text or "insufficient" in text


def is_voice_error(exc: Exception) -> bool:
    """Detect a rejected/invalid voice ID (e.g. StepFun ``voice_id_invalid``)."""

    text = str(exc).lower()
    return "voice_id" in text or "voice" in text


def retry_after(exc: Exception) -> float | None:
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
