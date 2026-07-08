"""Concurrency + lifecycle tests for KokoroEngine (offline, fake pipeline).

These cover two gaps the HTTP tests in test_app.py don't: that ``warm()`` never
propagates a load failure, and that the per-language pipeline cache builds each
language exactly once even when many first requests race (the ``_pipelines_lock``
guarantee). No torch, no weights — the pipeline factory is faked.
"""

from __future__ import annotations

import threading

import numpy as np

from lecturn_kokoro_server.engine import KokoroEngine


def _one_sample_pipe(text, *, voice, speed=1.0):
    yield type("S", (), {"audio": np.array([0.1], dtype=np.float32)})()


def test_warm_swallows_factory_errors():
    # warm() is a best-effort startup convenience: a failed weight download (here
    # a raising factory) must be logged and swallowed, never crash the server.
    def boom(lang_code, device):
        raise RuntimeError("weights download failed")

    engine = KokoroEngine(pipeline_factory=boom, device="cpu")
    engine.warm("af_heart")  # must NOT raise
    # Nothing was cached, so a real request would still surface the error.
    assert engine._pipelines == {}


def test_pipeline_built_once_per_lang_under_concurrent_first_calls():
    builds: list[str] = []
    builds_lock = threading.Lock()
    # Release all workers at once to maximise contention on the first build.
    barrier = threading.Barrier(8)

    def factory(lang_code, device):
        with builds_lock:
            builds.append(lang_code)
        return _one_sample_pipe

    engine = KokoroEngine(pipeline_factory=factory, device="cpu")

    def worker():
        barrier.wait()
        engine.synthesize("x", "af_heart")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Despite 8 concurrent first calls, lang "a" is built exactly once.
    assert builds.count("a") == 1
