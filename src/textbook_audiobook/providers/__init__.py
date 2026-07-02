"""Provider registry.

Every TTS backend is registered here as a single :class:`Provider` instance.
``--provider <name>`` on the CLI resolves through :func:`get_provider`.

To add a provider: implement a :class:`~textbook_audiobook.providers.base.Provider`
subclass (see ``stepfun.py`` / ``openrouter.py``) and add an instance to
``_PROVIDERS`` below. If it is OpenAI-SDK-compatible, no client changes are
needed — the generic client in :mod:`textbook_audiobook.tts` drives it.
"""

from __future__ import annotations

from textbook_audiobook.providers.base import ModelInfo, Provider
from textbook_audiobook.providers.openrouter import OpenRouterProvider
from textbook_audiobook.providers.stepfun import StepFunProvider

_PROVIDERS: dict[str, Provider] = {
    p.name: p for p in (StepFunProvider(), OpenRouterProvider())
}


class UnknownProviderError(KeyError):
    """Raised when a requested provider name is not registered."""


def get_provider(name: str) -> Provider:
    """Return the registered provider for ``name``.

    Raises :class:`UnknownProviderError` (a ``KeyError``) with the list of valid
    names when ``name`` is not registered.
    """

    try:
        return _PROVIDERS[name]
    except KeyError:
        valid = ", ".join(available_providers())
        raise UnknownProviderError(
            f"Unknown provider '{name}'. Available providers: {valid}."
        ) from None


def available_providers() -> list[str]:
    """Return the registered provider names in registration order."""

    return list(_PROVIDERS)


__all__ = [
    "ModelInfo",
    "Provider",
    "StepFunProvider",
    "OpenRouterProvider",
    "UnknownProviderError",
    "get_provider",
    "available_providers",
]
