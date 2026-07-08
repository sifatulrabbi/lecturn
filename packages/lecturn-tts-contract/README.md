# lecturn-tts-contract

A tiny, **dependency-free** package holding the one thing the lecturn CLI
(`textbook-audiobook`) and the local Kokoro server (`lecturn-kokoro-server`) must
agree on: the OpenAI-compatible **local TTS contract** — the Kokoro voice
catalogue, the model id, the default voice, the default host/port, the supported
response formats, and the input limits.

Both apps live in separate uv environments (the server pulls torch/kokoro; the
CLI must not). Before this package each re-declared these facts and the copies
drifted (the CLI knew 18 voices; the server served 54). Importing this single
module in both makes that drift impossible.

Consumed via a path dependency (`[tool.uv.sources]`) in both `pyproject.toml`s —
it is not published separately.

```python
from lecturn_tts_contract import (
    KOKORO_VOICES, VOICE_IDS, DEFAULT_VOICE, is_known_voice,
    MODEL_ID, DEFAULT_HOST, DEFAULT_PORT, DEFAULT_BASE_URL,
    DEFAULT_RESPONSE_FORMAT, SUPPORTED_FORMATS,
    KOKORO_MAX_INPUT_TOKENS, MAX_INPUT_CHARS,
)
```
