"""End-to-end pipeline tests driving the local provider through the seam.

Proves ``run_pipeline``'s ``client_factory`` seam works with ``LocalConfig`` +
``LocalTTSClient``, and that the resume cache — keyed on voice+format+text, not
provider — hits across runs. The network is stubbed to return real MP3 bytes,
exactly as the StepFun/OpenRouter pipeline tests do.
"""

from __future__ import annotations

import pytest

from textbook_audiobook import pipeline
from textbook_audiobook.config import LocalConfig
from textbook_audiobook.tts import LocalTTSClient


BOOK = """# A Small Book

## Chapter 1

The first chapter has a single, simple sentence to narrate.

## Chapter 2

The second chapter, likewise, is short and sweet.
"""


@pytest.fixture
def stub_network(monkeypatch, mp3_bytes):
    """Make LocalTTSClient return real MP3 bytes instead of calling the server."""

    counter = {"requests": 0}

    monkeypatch.setattr(LocalTTSClient, "_build_client", lambda self: object())

    def fake_request(self, text, model):
        counter["requests"] += 1
        return mp3_bytes

    monkeypatch.setattr(LocalTTSClient, "_request_audio", fake_request)
    return counter


def _config() -> LocalConfig:
    return LocalConfig(
        api_key="local",
        base_url="http://127.0.0.1:8880/v1",
        model="kokoro",
        voice="af_heart",
    )


def _factory(cfg):
    # The run_pipeline seam passes the config to the factory, so the client can
    # never drift from the config the cache is fingerprinted against.
    return LocalTTSClient(config=cfg)


def _write_book(tmp_path):
    src = tmp_path / "book.md"
    src.write_text(BOOK, encoding="utf-8")
    return src


def test_full_pipeline_via_local_seam(tmp_path, stub_network, mp3_duration_ms):
    src = _write_book(tmp_path)
    out_dir = tmp_path / "out"
    cfg = _config()

    result = pipeline.run_pipeline(
        src,
        out_dir,
        cfg,
        max_chars=1000,
        split_by_chapter=False,
        client_factory=_factory,
    )

    assert len(result.assembly.output_files) == 1
    out = result.assembly.output_files[0]
    assert out.exists() and mp3_duration_ms(out) > 0
    assert stub_network["requests"] == len(result.chunks) > 0
    # Self-hosted Kokoro is free ($0.00), so a real book still costs nothing —
    # proves estimate_cost resolved the local model to an explicit 0.0.
    assert result.estimated_cost_usd == 0.0


def test_resume_hits_cache_across_runs(tmp_path, stub_network):
    src = _write_book(tmp_path)
    out_dir = tmp_path / "out"
    cfg = _config()

    first = pipeline.run_pipeline(
        src, out_dir, cfg, max_chars=1000, client_factory=_factory
    )
    n = len(first.chunks)
    assert stub_network["requests"] == n

    # Second run reuses every cached chunk — nothing re-synthesized.
    pipeline.run_pipeline(
        src, out_dir, cfg, max_chars=1000, resume=True, client_factory=_factory
    )
    assert stub_network["requests"] == n
