"""Offline unit tests for the float32 -> MP3/WAV encode + normalization path.

Fully offline: synthesizes numpy arrays directly (no engine, no torch, no
weights) and decodes the encoded bytes with pydub's real ffmpeg backend. These
guard the two failure modes that make local audio sound wrong:

* wrong int16 scaling (forgetting ``* 32767`` -> near-silence, or doubling it ->
  clipped noise), and
* out-of-range samples wrapping around instead of clipping.

They also pin the peak-normalization target so the "raw Kokoro is too quiet"
fix can't silently regress.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from pydub import AudioSegment

from lecturn_kokoro_server import audio

SR = audio.SAMPLE_RATE


def _sine(amplitude: float, freq: float = 220.0, dur: float = 0.5) -> np.ndarray:
    t = np.linspace(0, dur, int(SR * dur), endpoint=False, dtype=np.float32)
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _decode_peak_dbfs(data: bytes, fmt: str) -> float:
    seg = AudioSegment.from_file(io.BytesIO(data), format=fmt)
    assert len(seg) > 0
    return seg.max_dBFS


def _decode_int16(data: bytes) -> np.ndarray:
    """Decode WAV bytes back to the raw int16 sample array (lossless path)."""

    seg = AudioSegment.from_file(io.BytesIO(data), format="wav")
    return np.array(seg.get_array_of_samples())


# -- correct int16 scaling (no normalization) ------------------------------


@pytest.mark.parametrize("fmt", ["mp3", "wav"])
def test_encode_scaling_is_full_amplitude(fmt: str) -> None:
    """A 0.5 sine must decode to ~-6 dBFS peak (20*log10(0.5)).

    Catches a missing ``* 32767`` (would be ~silence, tens of dB too low) and a
    double scaling (would clip to ~0 dBFS).
    """

    data = audio.encode(_sine(0.5), fmt, normalize=False)
    peak = _decode_peak_dbfs(data, fmt)
    assert peak == pytest.approx(-6.02, abs=1.0), f"{fmt} peak {peak:.2f} dBFS"


def test_encode_quiet_signal_stays_quiet_without_normalize() -> None:
    """Sanity floor: a -20 dBFS (0.1) sine must not read anywhere near 0 dBFS."""

    peak = _decode_peak_dbfs(audio.encode(_sine(0.1), "wav", normalize=False), "wav")
    assert peak == pytest.approx(-20.0, abs=1.0)


# -- out-of-range clipping (must not wrap) ---------------------------------


def test_out_of_range_clips_not_wraps() -> None:
    """>1.0 / <-1.0 samples must saturate to +/-full-scale, never wrap.

    Without the clip, ``2.0 * 32767`` overflows int16 and wraps to a tiny value
    (near silence / noise). The decoded extremes must be the int16 rails.
    """

    samples = np.array([0.0, 2.0, -2.0, 0.5, -0.5], dtype=np.float32)
    ints = _decode_int16(audio.encode(samples, "wav", normalize=False))
    assert ints.max() == 32767, ints.max()
    assert ints.min() == -32767, ints.min()


# -- peak normalization ----------------------------------------------------


@pytest.mark.parametrize("fmt", ["mp3", "wav"])
@pytest.mark.parametrize("amplitude", [0.05, 0.35, 0.9])
def test_normalize_lifts_peak_to_target(fmt: str, amplitude: float) -> None:
    """Any (non-silent) input peak-normalizes to ~TARGET_PEAK_DBFS.

    0.35 mirrors real Kokoro output; 0.05 is very quiet; 0.9 is already loud.
    All three must converge on the target, proving the fix both boosts quiet
    audio and keeps loud audio from clipping.
    """

    data = audio.encode(_sine(amplitude), fmt, normalize=True)
    peak = _decode_peak_dbfs(data, fmt)
    assert peak == pytest.approx(audio.TARGET_PEAK_DBFS, abs=1.0), (
        f"{fmt} amp={amplitude} peak {peak:.2f} dBFS"
    )


def test_peak_normalize_scalar_gain_preserves_shape() -> None:
    """Normalization is a single positive gain: the waveform ratios are intact."""

    sine = _sine(0.2)
    out = audio.peak_normalize(sine)
    assert float(np.max(np.abs(out))) == pytest.approx(audio._TARGET_PEAK, abs=1e-4)
    # Same shape, scaled by one constant -> element-wise ratio is uniform.
    nonzero = np.abs(sine) > 1e-6
    ratios = out[nonzero] / sine[nonzero]
    assert np.allclose(ratios, ratios[0], atol=1e-4)


def test_peak_normalize_leaves_silence_untouched() -> None:
    """Near-silent input is returned unchanged (no divide-by-zero, no noise boost)."""

    silence = np.zeros(1000, dtype=np.float32)
    assert np.array_equal(audio.peak_normalize(silence), silence)

    tiny = np.full(1000, 1e-6, dtype=np.float32)  # below _SILENCE_FLOOR
    assert np.array_equal(audio.peak_normalize(tiny), tiny)


def test_encode_silence_is_still_decodable() -> None:
    """Encoding silence must not crash or produce garbage (normalize default on)."""

    data = audio.encode(np.zeros(2400, dtype=np.float32), "wav")
    seg = AudioSegment.from_file(io.BytesIO(data), format="wav")
    assert len(seg) > 0
