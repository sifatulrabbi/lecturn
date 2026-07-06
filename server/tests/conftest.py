"""Offline test fixtures.

The whole point: exercise the real engine (segment concatenation), audio
encoding (real ffmpeg-backed mp3/wav), and HTTP surface WITHOUT importing torch,
downloading weights, or touching a GPU. We do that by injecting a *fake pipeline
factory* into the real :class:`KokoroEngine` and pinning the device to "cpu".

The fake pipeline returns canned float32 numpy audio and records how it was
called, so tests can assert multi-segment ordering and speed pass-through.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lecturn_kokoro_server.app import create_app
from lecturn_kokoro_server.engine import KokoroEngine


class _Segment:
    """Mimics a kokoro Result: exposes an ``.audio`` numpy array."""

    def __init__(self, audio: np.ndarray) -> None:
        self.audio = audio


class FakePipeline:
    """Stand-in for ``KPipeline``. Yields fixed segments; records call args.

    Produces two segments (a short sine, then a shorter one) so tests can verify
    that *all* segments are concatenated in order. Amplitude is real (not pure
    silence) so the encoded mp3/wav is genuinely decodable.
    """

    def __init__(self, lang_code: str, device: str) -> None:
        self.lang_code = lang_code
        self.device = device
        self.calls: list[dict[str, object]] = []
        sr = 24_000
        t1 = np.linspace(0, 0.20, int(sr * 0.20), endpoint=False, dtype=np.float32)
        t2 = np.linspace(0, 0.10, int(sr * 0.10), endpoint=False, dtype=np.float32)
        self.segments = [
            (0.3 * np.sin(2 * np.pi * 220.0 * t1)).astype(np.float32),
            (0.3 * np.sin(2 * np.pi * 330.0 * t2)).astype(np.float32),
        ]

    def __call__(self, text, *, voice, speed=1.0):
        self.calls.append({"text": text, "voice": voice, "speed": speed})
        for seg in self.segments:
            yield _Segment(seg)


@pytest.fixture
def pipelines() -> dict[str, FakePipeline]:
    """Records every FakePipeline the engine builds, keyed by lang_code."""

    return {}


@pytest.fixture
def engine(pipelines: dict[str, FakePipeline]) -> KokoroEngine:
    """A real engine wired to the fake pipeline factory, pinned to CPU."""

    def factory(lang_code: str, device: str) -> FakePipeline:
        pipe = FakePipeline(lang_code, device)
        pipelines[lang_code] = pipe
        return pipe

    return KokoroEngine(pipeline_factory=factory, device="cpu")


@pytest.fixture
def client(engine: KokoroEngine) -> TestClient:
    return TestClient(create_app(engine))
