"""Configuration and constants for the StepFun TTS integration.

Secrets are read from the environment only — never hard-coded. See PLAN.md
("API Key") for the accepted environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# StepFun's documented hard cap, per the Audio Usage Limits page. This is an
# API constraint, NOT a tuning parameter — chunks must never exceed it.
HARD_CHAR_LIMIT: int = 1000

DEFAULT_BASE_URL: str = "https://api.stepfun.ai/v1"
DEFAULT_MODEL: str = "stepaudio-2.5-tts"
ECONOMY_MODEL: str = "step-tts-2"
# StepFun uses its own voice IDs (NOT OpenAI names like "alloy"). This default
# is a voice ID documented in StepFun's own API examples.
DEFAULT_VOICE: str = "cixingnansheng"
DEFAULT_RESPONSE_FORMAT: str = "mp3"


@dataclass(frozen=True)
class ModelInfo:
    """Metadata for a TTS model, including pricing for cost estimation."""

    name: str
    # USD per 10,000 characters (PLAN.md "Pricing").
    price_per_10k_chars: float
    description: str


# Confirmed models and pricing from PLAN.md.
MODELS: dict[str, ModelInfo] = {
    "stepaudio-2.5-tts": ModelInfo(
        name="stepaudio-2.5-tts",
        price_per_10k_chars=0.85,
        description="Default model, best audio quality.",
    ),
    "step-tts-2": ModelInfo(
        name="step-tts-2",
        price_per_10k_chars=0.40,
        description="Economy alternative, lower cost.",
    ),
    "step-tts-mini": ModelInfo(
        name="step-tts-mini",
        # Pricing not published in PLAN.md; treated as economy-tier for
        # estimation purposes until confirmed from live docs.
        price_per_10k_chars=0.40,
        description="Compact model observed in session notes (pricing unconfirmed).",
    ),
}

# StepFun's own voice catalogue (voice IDs accepted by the "voice" parameter),
# sourced from StepFun's TTS docs. These are NOT OpenAI voice names. Surfaced
# via `--list-voices`. StepFun also supports voice cloning (out of scope for
# v1), and the available set may change — validate against a live call.
VOICES: dict[str, str] = {
    # Male voices
    "cixingnansheng": "Magnetic Male (StepFun docs default example)",
    "wenrounansheng": "Gentle Male",
    "wenrougongzi": "Tender Gentleman",
    "yuanqinansheng": "Spirited Male",
    "ruyananshi": "Scholarly Gentleman",
    "boyinnansheng": "Broadcast Male",
    "shenchennanyin": "Deep Male",
    "zixinnansheng": "Confident Male",
    "shuangkuainansheng": "Straightforward Male",
    "zhengpaiqingnian": "Upright Youth",
    "magnetic-voiced-male": "Magnetic Male (English-keyed)",
    "soft-spoken-gentleman": "Soft-spoken Gentleman (English-keyed)",
    # Female voices
    "qingchunshaonv": "Pure Girl",
    "yuanqishaonv": "Spirited Girl",
    "linjiajiejie": "Girl Next Door",
    "jingdiannvsheng": "Classic Female",
    "tianmeinvsheng": "Sweet Female",
    "wenrounvsheng": "Gentle Female",
    "ruanmengnvsheng": "Cute Soft Female",
    "ganliannvsheng": "Capable Female",
    "qinhenvsheng": "Warm Female",
    "youyanvsheng": "Elegant Female",
    "lengyanyujie": "Cool Beauty",
    "zhixingjiejie": "Intellectual Lady",
    "shuangkuaijiejie": "Bold Sister",
    "qinqienvsheng": "Friendly Female",
    "jilingshaonv": "Clever Girl",
    "qingniandaxuesheng": "College Student",
    "lively-girl": "Lively Girl (English-keyed)",
    "elegantgentle-female": "Elegant Gentle Female (English-keyed)",
    "vibrant-youth": "Vibrant Youth (English-keyed)",
}

# Ordered list of voice IDs (keys of VOICES) for convenience.
KNOWN_VOICES: list[str] = list(VOICES)


@dataclass
class StepFunConfig:
    """Resolved runtime configuration for talking to StepFun."""

    api_key: str
    base_url: str
    model: str
    voice: str
    response_format: str = DEFAULT_RESPONSE_FORMAT

    @classmethod
    def from_env(
        cls,
        *,
        model: str = DEFAULT_MODEL,
        voice: str = DEFAULT_VOICE,
        response_format: str = DEFAULT_RESPONSE_FORMAT,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> "StepFunConfig":
        resolved_key = api_key or resolve_api_key()
        if not resolved_key:
            raise MissingApiKeyError(
                "No StepFun API key found. Set the STEPFUN_API_KEY environment "
                "variable (or STEPFUN_STEP_PLAN_API_KEY)."
            )
        resolved_base = (
            base_url
            or os.environ.get("STEPFUN_BASE_URL")
            or DEFAULT_BASE_URL
        )
        return cls(
            api_key=resolved_key,
            base_url=resolved_base,
            model=model,
            voice=voice,
            response_format=response_format,
        )


class MissingApiKeyError(RuntimeError):
    """Raised when no usable StepFun API key is present in the environment."""


def resolve_api_key() -> str | None:
    """Return the first available StepFun API key from the environment."""

    return (
        os.environ.get("STEPFUN_API_KEY")
        or os.environ.get("STEPFUN_STEP_PLAN_API_KEY")
    )


def estimate_cost(char_count: int, model: str) -> float:
    """Estimate USD cost for narrating ``char_count`` characters with ``model``."""

    info = MODELS.get(model)
    if info is None:
        return 0.0
    return (char_count / 10_000) * info.price_per_10k_chars
