"""Runtime configuration for talking to a TTS provider.

Provider-specific catalogues, defaults, pricing, and error advice live in
:mod:`textbook_audiobook.providers`. This module only holds the resolved,
provider-agnostic runtime config (:class:`TTSConfig`) that the pipeline and the
client consume.

Secrets are read from the environment only — never hard-coded. Each provider
declares its own key/base-URL environment variables (see
``providers/*.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

from textbook_audiobook.providers.base import Provider

DEFAULT_RESPONSE_FORMAT: str = "mp3"


class MissingApiKeyError(RuntimeError):
    """Raised when no usable API key is present in the environment."""


@dataclass
class TTSConfig:
    """Resolved runtime configuration for a single conversion run.

    Carries a reference to the selected :class:`~.providers.base.Provider` plus
    the concrete connection fields the OpenAI-compatible client needs. The
    per-request character cap is sourced from the provider so it can never drift
    from the catalogue.
    """

    provider: Provider
    api_key: str
    base_url: str
    model: str
    voice: str
    response_format: str = DEFAULT_RESPONSE_FORMAT

    @property
    def hard_char_limit(self) -> int:
        return self.provider.hard_char_limit

    @classmethod
    def resolve(
        cls,
        provider: Provider,
        *,
        model: str | None = None,
        voice: str | None = None,
        response_format: str = DEFAULT_RESPONSE_FORMAT,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> "TTSConfig":
        """Build a config, filling unset fields from the provider's defaults.

        The API key is read from the provider's environment variables unless one
        is passed explicitly; a missing key raises :class:`MissingApiKeyError`
        naming the expected variable.
        """

        resolved_key = api_key or provider.resolve_api_key()
        if not resolved_key:
            expected = " or ".join(provider.api_key_env)
            raise MissingApiKeyError(
                f"No {provider.label} API key found. Set the {expected} "
                "environment variable."
            )
        return cls(
            provider=provider,
            api_key=resolved_key,
            base_url=provider.resolve_base_url(base_url),
            model=model or provider.default_model,
            voice=voice or provider.default_voice,
            response_format=response_format,
        )
