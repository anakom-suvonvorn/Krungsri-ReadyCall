"""TTS adapter selection.

Deliberately tiny, and deliberately here rather than in `api/deps.py`: the only caller
is `scripts/build_prompts.py`, which runs at build time and must not drag in the web
stack to render a sentence (`D2`). During a v1 call nothing in this package runs at all
— telephony plays a file (`D24`).
"""

from __future__ import annotations

from readycall.config import Settings, TtsEngineName
from readycall.errors import ConfigError
from readycall.ports.tts import TtsEngine


def build_tts(settings: Settings) -> TtsEngine:
    if settings.tts_engine in (TtsEngineName.NULL, TtsEngineName.PRERENDERED):
        # `prerendered` means "play what the pack already holds", so the build-time engine
        # behind it is still the null one: it records the line and synthesises nothing.
        from readycall.adapters.tts.null import NullTtsEngine

        return NullTtsEngine()
    raise ConfigError(
        f"TTS_ENGINE={settings.tts_engine.value} has no adapter yet. A real voice is "
        "chosen on a listening test of the actual prompts (INTEGRATIONS.md 4), and "
        "until then the pack renders with the null engine."
    )


__all__ = ["build_tts"]
