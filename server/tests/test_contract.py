"""End-to-end SDK <-> server wire-contract test (fully offline).

This is the ONLY test that exercises the shared ``lecturn_tts_contract`` across
the real boundary it exists to protect: it drives the real FastAPI app through
the **OpenAI Python SDK** — the exact transport lecturn's ``LocalTTSClient`` uses
internally — over an in-process ASGI transport (no real socket, no torch; the
engine is the same fake pipeline the other server tests use, via conftest's
``client`` / ``engine`` fixtures). It proves that a request built purely from the
contract's constants (model id, default voice, default response format) is
accepted and returns real audio, and that the server's advertised ``/v1/models``
and ``/v1/audio/voices`` surface matches the contract exactly — so if either
app's copy of these facts ever drifts, this test fails.

Note on the transport: we drive the SDK over Starlette's ``TestClient`` (an
``httpx.Client`` subclass that bridges to the ASGI app in-process) rather than a
bare ``httpx.ASGITransport`` — the latter is async-only, so a *synchronous*
``httpx.Client`` (which the sync OpenAI SDK requires) cannot use it directly.
The TestClient gives the same real, socket-free ASGI round-trip.
"""

from __future__ import annotations

import lecturn_tts_contract as contract
import pytest
from openai import OpenAI


@pytest.fixture
def oai(client) -> OpenAI:
    """The OpenAI SDK client — the exact transport lecturn's LocalTTSClient uses.

    Built on conftest's ``client`` (a Starlette ``TestClient`` wired to the real
    app + fake engine), so no torch is imported and no socket is opened.
    """

    # The SDK needs a non-empty key; the server ignores Authorization entirely.
    return OpenAI(api_key="local", base_url="http://testserver/v1", http_client=client)


def _is_mp3(data: bytes) -> bool:
    """True if ``data`` starts with an ID3 tag or an MPEG frame sync."""

    if data[:3] == b"ID3":
        return True
    return len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def test_sdk_speech_roundtrip_uses_contract_constants(oai: OpenAI):
    """A request built only from the contract's constants returns real mp3."""

    resp = oai.audio.speech.create(
        model=contract.MODEL_ID,
        voice=contract.DEFAULT_VOICE,
        input="Hello.",
        response_format=contract.DEFAULT_RESPONSE_FORMAT,
    )
    data = resp.read()
    assert _is_mp3(data), data[:8]


def test_advertised_models_match_contract(oai: OpenAI):
    """GET /v1/models advertises exactly the contract's model id."""

    models = oai.models.list()
    assert [m.id for m in models.data] == [contract.MODEL_ID]


def test_advertised_voices_match_contract(client):
    """GET /v1/audio/voices advertises exactly the contract's voice list.

    Not a standard OpenAI operation, so it goes through the same ASGI-backed
    TestClient the SDK is driven over rather than an SDK helper.
    """

    body = client.get("/v1/audio/voices").json()
    assert body["voices"] == contract.VOICE_IDS
