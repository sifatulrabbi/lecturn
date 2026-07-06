"""Encode Kokoro's raw model output into MP3/WAV container bytes.

Kokoro emits 24 kHz mono float32 PCM in ``[-1.0, 1.0]``. We convert to 16-bit
PCM and hand it to pydub, which muxes WAV in-process and shells out to ffmpeg
for MP3. The MP3 path is the one lecturn depends on: its resume cache validates
the response by magic bytes, so the output MUST start with an ID3 tag or an
MPEG frame sync — both of which ffmpeg's libmp3lame output satisfies.

**Loudness.** Raw Kokoro output is quiet: real synthesis peaks around
-9 dBFS (RMS ~-27 dBFS), so as-shipped MP3s sound much softer than the hosted
providers' normalized audio. We therefore peak-normalize each request to
:data:`TARGET_PEAK_DBFS` before the int16 conversion. This is a gain change
only — the clip guard below still protects against any out-of-range sample.
"""

from __future__ import annotations

import io

import numpy as np

# Kokoro-82M always outputs 24 kHz mono.
SAMPLE_RATE: int = 24_000

# Peak-normalization target. Kokoro's raw peaks sit near -9 dBFS, which is
# audibly quieter than the hosted providers; we lift each request so its loudest
# sample lands here. -1.5 dBFS leaves a sliver of headroom so libmp3lame's
# inter-sample overshoot doesn't clip.
TARGET_PEAK_DBFS: float = -1.5
_TARGET_PEAK: float = float(10.0 ** (TARGET_PEAK_DBFS / 20.0))  # ~0.8414

# Below this peak the array is effectively silence (~-80 dBFS); normalizing it
# would just amplify the noise floor, so we leave it untouched.
_SILENCE_FLOOR: float = 1e-4

# response_format -> (pydub export format, HTTP Content-Type).
_FORMATS: dict[str, tuple[str, str]] = {
    "mp3": ("mp3", "audio/mpeg"),
    "wav": ("wav", "audio/wav"),
}

SUPPORTED_FORMATS: tuple[str, ...] = tuple(_FORMATS)


class UnsupportedFormatError(ValueError):
    """Raised for a ``response_format`` other than mp3/wav."""


def content_type_for(fmt: str) -> str:
    """Return the HTTP Content-Type for a supported ``response_format``."""

    try:
        return _FORMATS[fmt][1]
    except KeyError as exc:  # pragma: no cover - guarded by the route first
        raise UnsupportedFormatError(fmt) from exc


def peak_normalize(mono: np.ndarray) -> np.ndarray:
    """Scale ``mono`` so its loudest sample sits at :data:`TARGET_PEAK_DBFS`.

    Gain-only: multiplies by a single positive scalar, so it can't distort or
    change the waveform's shape. Near-silent input (peak below
    :data:`_SILENCE_FLOOR`) is returned unchanged so we don't amplify the noise
    floor of an effectively-silent request.
    """

    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak <= _SILENCE_FLOOR:
        return mono
    return mono * (_TARGET_PEAK / peak)


def encode(
    samples: np.ndarray,
    fmt: str,
    *,
    sample_rate: int = SAMPLE_RATE,
    normalize: bool = True,
) -> bytes:
    """Encode a float32 PCM array into container ``fmt`` bytes.

    ``samples`` is 1-D mono float32 in ``[-1, 1]`` (values outside are clipped).
    Unless ``normalize`` is disabled, the array is peak-normalized to
    :data:`TARGET_PEAK_DBFS` first (raw Kokoro output is quiet). Returns the full
    container payload (MP3 with an ID3/frame-sync header, or a RIFF/WAVE file).
    Raises :class:`UnsupportedFormatError` for anything but mp3/wav so the caller
    can map it to an HTTP 400.
    """

    export_format = _FORMATS.get(fmt, (None,))[0]
    if export_format is None:
        raise UnsupportedFormatError(fmt)

    # pydub is imported lazily so importing this module stays cheap and the
    # dependency surface is obvious at the point of use.
    from pydub import AudioSegment

    mono = np.asarray(samples, dtype=np.float32).reshape(-1)
    if normalize:
        mono = peak_normalize(mono)
    # float32 [-1, 1] -> int16 PCM. Clip first so a stray >1.0 sample can't wrap
    # around into loud noise. (Normalization lands the peak below 1.0, so this
    # only bites the normalize=False path or a NaN/inf, but keep it as a guard.)
    pcm16 = (np.clip(mono, -1.0, 1.0) * 32767.0).astype("<i2")

    segment = AudioSegment(
        data=pcm16.tobytes(),
        sample_width=2,
        frame_rate=sample_rate,
        channels=1,
    )
    buffer = io.BytesIO()
    segment.export(buffer, format=export_format)
    return buffer.getvalue()
