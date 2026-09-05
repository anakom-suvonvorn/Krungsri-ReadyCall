"""Ports: `Protocol` definitions only — no implementations, no vendor imports.

Business logic in `services/` imports from here and nowhere else for anything
external. That single rule is what turns "the hackathon data is not what we assumed"
from a rewrite into a config change (`D3`).
"""

from readycall.ports.blob_storage import BlobStorage, StoredObject
from readycall.ports.core_data import CoreDataProvider
from readycall.ports.event_bus import EventBus, Handler
from readycall.ports.keyring import DataKey, KeyRing
from readycall.ports.llm import LlmClient, LlmResult, LlmUsage, PromptRef
from readycall.ports.stt import AudioFrame, EngineInfo, SttEngine, SttHint, SttResult
from readycall.ports.telephony import (
    CallLeg,
    DialTarget,
    MediaFork,
    TelephonyEvent,
    TelephonyEventKind,
    TelephonyProvider,
)
from readycall.ports.tts import SynthesizedAudio, TtsEngine, VoiceSpec

__all__ = [
    "AudioFrame",
    "BlobStorage",
    "CallLeg",
    "CoreDataProvider",
    "DataKey",
    "DialTarget",
    "EngineInfo",
    "EventBus",
    "Handler",
    "KeyRing",
    "LlmClient",
    "LlmResult",
    "LlmUsage",
    "MediaFork",
    "PromptRef",
    "StoredObject",
    "SttEngine",
    "SttHint",
    "SttResult",
    "SynthesizedAudio",
    "TelephonyEvent",
    "TelephonyEventKind",
    "TelephonyProvider",
    "TtsEngine",
    "VoiceSpec",
]
