"""FastAPI app exposing the OpenAI-compatible TTS surface lecturn speaks to.

Endpoints (mounted so lecturn's default base URL ``http://127.0.0.1:8880/v1``
resolves correctly):

* ``POST /v1/audio/speech`` — synthesize ``input`` with ``voice`` into mp3
  (default) or wav bytes.
* ``GET  /v1/models``       — OpenAI-shaped list containing ``kokoro``.
* ``GET  /v1/audio/voices`` — ``{"voices": [...]}`` (Kokoro-FastAPI shape).
* ``GET  /health``          — liveness + selected torch device.

No authentication: any ``Authorization`` header is ignored (lecturn sends a
placeholder Bearer that the OpenAI SDK requires). The server binds ``127.0.0.1``
by default (see :mod:`cli`); exposing it more widely is the operator's own risk.

The engine is stored on ``app.state.engine`` and can be supplied to
:func:`create_app` — tests inject a fake so importing this module never imports
torch or downloads weights.
"""

from __future__ import annotations

import logging
import os

# Apple Silicon: some Kokoro ops fall back to CPU. Enable it before torch is
# ever imported. setdefault so an operator override wins. Set here (not only in
# cli.py) so the community `uvicorn lecturn_kokoro_server.app:app` path is
# covered too.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from lecturn_kokoro_server import audio, voices
from lecturn_kokoro_server.engine import KokoroEngine

logger = logging.getLogger(__name__)

# Single-model server. We accept any `model` value (see the route) but advertise
# this canonical id, which is also what Kokoro-FastAPI reports.
MODEL_ID: str = "kokoro"


class SpeechRequest(BaseModel):
    """Body of ``POST /v1/audio/speech`` (OpenAI ``audio.speech.create`` shape).

    Unknown extra fields are ignored (pydantic default) so future OpenAI-SDK
    additions don't 422 the request.
    """

    input: str
    voice: str = voices.DEFAULT_VOICE
    # Single-model server: `model` is accepted but not used for routing.
    model: str = MODEL_ID
    response_format: str = "mp3"
    # Kokoro supports playback speed; lecturn doesn't send it, so default 1.0.
    speed: float = Field(default=1.0, gt=0)


def create_app(engine: KokoroEngine | None = None) -> FastAPI:
    """Build the FastAPI app, wiring in ``engine`` (or a real one by default)."""

    app = FastAPI(
        title="lecturn-kokoro-server",
        summary="Local OpenAI-compatible Kokoro-82M TTS for lecturn --provider local.",
        version="0.1.0",
    )
    # A real engine here is cheap: it imports no torch and loads no weights until
    # the first synthesis (or an explicit warm).
    app.state.engine = engine if engine is not None else KokoroEngine()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "device": app.state.engine.device}

    @app.get("/v1/models")
    def list_models() -> dict[str, object]:
        created = int(time.time())
        return {
            "object": "list",
            "data": [
                {
                    "id": MODEL_ID,
                    "object": "model",
                    "created": created,
                    "owned_by": "lecturn-kokoro-server",
                }
            ],
        }

    @app.get("/v1/audio/voices")
    def list_voices() -> dict[str, list[str]]:
        # Kokoro-FastAPI-compatible shape: a flat list under "voices".
        return {"voices": voices.VOICE_IDS}

    @app.post("/v1/audio/speech")
    def create_speech(req: SpeechRequest) -> Response:
        text = req.input.strip()
        if not text:
            raise HTTPException(status_code=400, detail="`input` must not be empty.")

        fmt = req.response_format.lower()
        if fmt not in audio.SUPPORTED_FORMATS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported response_format {req.response_format!r}; "
                    f"supported: {', '.join(audio.SUPPORTED_FORMATS)}."
                ),
            )

        if not voices.is_known_voice(req.voice):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown voice {req.voice!r}. "
                    "Call GET /v1/audio/voices for the available Kokoro voice IDs "
                    "(e.g. 'af_heart')."
                ),
            )

        if req.model != MODEL_ID:
            # Single-model server: honour the request regardless, but note it.
            logger.info(
                "Request model %r != %r; serving Kokoro anyway.", req.model, MODEL_ID
            )

        try:
            samples = app.state.engine.synthesize(text, req.voice, speed=req.speed)
            data = audio.encode(samples, fmt)
        except audio.UnsupportedFormatError as exc:  # pragma: no cover - guarded above
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Synthesis failed for voice %r.", req.voice)
            raise HTTPException(
                status_code=500, detail=f"Synthesis failed: {exc}"
            ) from exc

        return Response(content=data, media_type=audio.content_type_for(fmt))

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        # OpenAI-style error envelope so any OpenAI-compatible client (lecturn's
        # SDK included) surfaces `detail` cleanly.
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"message": exc.detail, "type": "invalid_request_error"}},
        )

    return app


# Module-level app for `uvicorn lecturn_kokoro_server.app:app` and community
# tooling. Building it is cheap (lazy engine); it imports no torch.
app = create_app()
