"""lecturn-kokoro-server — a local, OpenAI-compatible Kokoro-82M TTS server.

Wraps the official ``kokoro`` PyTorch package behind the same
``POST /v1/audio/speech`` wire shape lecturn already speaks to StepFun and
OpenRouter, so ``lecturn convert book.pdf --provider local`` narrates entirely
on the user's own hardware — no paid API, no per-request cost.

The heavy ML stack (torch, kokoro, misaki) lives only in this app's own venv;
it is deliberately kept out of lecturn's runtime install.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
