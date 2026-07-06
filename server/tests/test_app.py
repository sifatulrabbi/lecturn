"""HTTP + engine tests for the local Kokoro server (fully offline)."""

from __future__ import annotations

import io

import numpy as np
from pydub import AudioSegment

from lecturn_kokoro_server import voices
from lecturn_kokoro_server.engine import KokoroEngine


def _is_mp3(data: bytes) -> bool:
    """True if ``data`` starts with an ID3 tag or an MPEG frame sync."""

    if data[:3] == b"ID3":
        return True
    # MPEG audio frame sync: 11 set bits (0xFF followed by 0xEx/0xFx).
    return len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


# -- /v1/audio/speech: mp3 -------------------------------------------------


def test_speech_returns_real_mp3(client):
    resp = client.post(
        "/v1/audio/speech",
        json={"model": "kokoro", "input": "Hello world.", "voice": "af_heart"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "audio/mpeg"
    body = resp.content
    assert _is_mp3(body), body[:8]
    # Decodable and non-empty (this exercises the real ffmpeg mp3 path).
    seg = AudioSegment.from_file(io.BytesIO(body), format="mp3")
    assert len(seg) > 0


def test_speech_defaults_response_format_to_mp3(client):
    resp = client.post(
        "/v1/audio/speech",
        json={"input": "Default format.", "voice": "af_heart"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "audio/mpeg"
    assert _is_mp3(resp.content)


# -- /v1/audio/speech: wav -------------------------------------------------


def test_speech_wav(client):
    resp = client.post(
        "/v1/audio/speech",
        json={"input": "Wav please.", "voice": "af_heart", "response_format": "wav"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "audio/wav"
    body = resp.content
    assert body[:4] == b"RIFF" and body[8:12] == b"WAVE"
    seg = AudioSegment.from_file(io.BytesIO(body), format="wav")
    assert len(seg) > 0


# -- 400s ------------------------------------------------------------------


def test_bad_voice_400(client):
    resp = client.post(
        "/v1/audio/speech",
        json={"input": "Hi.", "voice": "not_a_voice"},
    )
    assert resp.status_code == 400
    assert "not_a_voice" in resp.text


def test_bad_format_400(client):
    resp = client.post(
        "/v1/audio/speech",
        json={"input": "Hi.", "voice": "af_heart", "response_format": "flac"},
    )
    assert resp.status_code == 400
    assert "flac" in resp.text


def test_empty_input_400(client):
    resp = client.post(
        "/v1/audio/speech",
        json={"input": "   ", "voice": "af_heart"},
    )
    assert resp.status_code == 400
    assert "input" in resp.text.lower()


# -- unknown model accepted ------------------------------------------------


def test_unknown_model_is_accepted(client):
    resp = client.post(
        "/v1/audio/speech",
        json={"model": "some-other-model", "input": "Hi.", "voice": "af_heart"},
    )
    assert resp.status_code == 200, resp.text
    assert _is_mp3(resp.content)


# -- speed pass-through ----------------------------------------------------


def test_speed_is_passed_to_pipeline(client, pipelines):
    resp = client.post(
        "/v1/audio/speech",
        json={"input": "Faster.", "voice": "af_heart", "speed": 1.5},
    )
    assert resp.status_code == 200, resp.text
    # 'a' == lang_code for af_heart.
    assert pipelines["a"].calls[-1]["speed"] == 1.5


def test_nonpositive_speed_422(client):
    resp = client.post(
        "/v1/audio/speech",
        json={"input": "Hi.", "voice": "af_heart", "speed": 0},
    )
    assert resp.status_code == 422


# -- catalogue endpoints ---------------------------------------------------


def test_list_voices(client):
    resp = client.get("/v1/audio/voices")
    assert resp.status_code == 200
    body = resp.json()
    assert "af_heart" in body["voices"]
    assert len(body["voices"]) == 54


def test_list_models(client):
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert [m["id"] for m in body["data"]] == ["kokoro"]


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["device"] == "cpu"


# -- engine: multi-segment concatenation ordering --------------------------


def test_engine_concatenates_segments_in_order():
    seg_a = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    seg_b = np.array([0.4, 0.5], dtype=np.float32)
    seg_c = np.array([0.6], dtype=np.float32)

    class _Seg:
        def __init__(self, audio):
            self.audio = audio

    def factory(lang_code, device):
        assert lang_code == "a"

        def pipe(text, *, voice, speed=1.0):
            yield _Seg(seg_a)
            yield _Seg(seg_b)
            yield _Seg(seg_c)

        return pipe

    engine = KokoroEngine(pipeline_factory=factory, device="cpu")
    out = engine.synthesize("whatever", "af_heart")
    assert np.array_equal(out, np.concatenate([seg_a, seg_b, seg_c]))


def test_engine_accepts_tuple_segments_and_skips_none():
    """kokoro's older ``(gs, ps, audio)`` tuple shape, with a pause (None)."""

    def factory(lang_code, device):
        def pipe(text, *, voice, speed=1.0):
            yield ("gs", "ps", np.array([0.1, 0.2], dtype=np.float32))
            yield ("gs", "ps", None)  # a pause: no audio
            yield ("gs", "ps", np.array([0.3], dtype=np.float32))

        return pipe

    engine = KokoroEngine(pipeline_factory=factory, device="cpu")
    out = engine.synthesize("x", "bm_george")
    assert np.array_equal(out, np.array([0.1, 0.2, 0.3], dtype=np.float32))


def test_engine_lang_code_routing():
    seen: list[str] = []

    def factory(lang_code, device):
        seen.append(lang_code)

        def pipe(text, *, voice, speed=1.0):
            yield type("S", (), {"audio": np.array([0.0], dtype=np.float32)})()

        return pipe

    engine = KokoroEngine(pipeline_factory=factory, device="cpu")
    engine.synthesize("x", "af_heart")  # -> 'a'
    engine.synthesize("x", "bm_george")  # -> 'b'
    engine.synthesize("x", "af_bella")  # -> 'a' again (cached, no rebuild)
    assert seen == ["a", "b"]
