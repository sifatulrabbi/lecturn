"""``lecturn-kokoro`` console entrypoint — start the local TTS server.

Binds ``127.0.0.1:8880`` by default (lecturn's ``--provider local`` base URL).
Sets ``PYTORCH_ENABLE_MPS_FALLBACK=1`` before torch is ever imported so Apple
Silicon works out of the box, then hands off to uvicorn.
"""

from __future__ import annotations

import argparse
import logging
import os

import lecturn_tts_contract as contract

# Must be set before torch is imported (which happens lazily on first synthesis).
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

# From the shared contract so the server binds where lecturn's `--provider local`
# looks by default.
DEFAULT_HOST = contract.DEFAULT_HOST
DEFAULT_PORT = contract.DEFAULT_PORT


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lecturn-kokoro",
        description=(
            "Local OpenAI-compatible Kokoro-82M TTS server for lecturn "
            "(point lecturn at it with `--provider local`)."
        ),
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=(
            "Interface to bind (default: 127.0.0.1). Binding a wider address "
            "exposes an unauthenticated server — your own risk."
        ),
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="Port to bind (default: 8880)."
    )
    parser.add_argument(
        "--log-level",
        default="info",
        help="uvicorn log level (default: info).",
    )
    parser.add_argument(
        "--warm",
        action="store_true",
        help=(
            "Load the model at startup (triggers the ~327 MB weight download on "
            "first run) so the first request isn't slow."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Imported here (after env + logging are set) — importing the app is cheap
    # and imports no torch.
    from lecturn_kokoro_server.app import app

    logger = logging.getLogger("lecturn_kokoro_server")
    logger.info(
        "Starting lecturn-kokoro on http://%s:%d (device: %s). "
        "Point lecturn at it with: lecturn convert BOOK --provider local",
        args.host,
        args.port,
        app.state.engine.device,
    )
    if args.warm:
        logger.info("Warming model (this may take a while on first run)...")
        app.state.engine.warm()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":  # pragma: no cover
    main()
