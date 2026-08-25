"""Settings: every adapter choice and every tunable, from the environment.

Field names match the documented `.env` surface (`INTEGRATIONS.md` §7) one-for-one,
deliberately flat rather than nested, so what you read in the docs is what you set.

Nothing in this system hardcodes an endpoint, a model name, a threshold or a weight
(`CLAUDE.md`). Adapter selection is an enum so a typo fails at startup, not mid-call.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from readycall.errors import ConfigError


class TelephonyProviderName(StrEnum):
    SIMULATED = "simulated"
    ASTERISK = "asterisk"
    TWILIO = "twilio"
    LIVEKIT = "livekit"


class SttEngineName(StrEnum):
    SCRIPTED = "scripted"
    THONBURIAN_HF = "thonburian_hf"
    THONBURIAN_CT2 = "thonburian_ct2"
    DISTILL = "distill"
    TYPHOON = "typhoon"
    CLOUD = "cloud"


class LlmProviderName(StrEnum):
    RULEBASED = "rulebased"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"
    GEMINI = "gemini"


class TtsEngineName(StrEnum):
    NULL = "null"
    PRERENDERED = "prerendered"
    AZURE = "azure"


class CoreDataProviderName(StrEnum):
    NULL = "null"
    FIXTURES = "fixtures"
    MOCK_POSTGRES = "mock_postgres"
    HTTP_API = "http_api"
    SQL_PASSTHROUGH = "sql_passthrough"


class IntakeStrategyName(StrEnum):
    PASSIVE = "passive"
    GUIDED = "guided"
    CONVERSATIONAL = "conversational"


class EventBusName(StrEnum):
    MEMORY = "memory"
    REDIS = "redis"
    KAFKA = "kafka"


class BlobStorageName(StrEnum):
    MEMORY = "memory"
    LOCALFS = "localfs"
    MINIO = "minio"
    S3 = "s3"


class MatcherSolverName(StrEnum):
    HUNGARIAN = "hungarian"
    GREEDY = "greedy"
    FIFO = "fifo"


class AcceptMode(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- environment ---
    env: str = "dev"
    debug: bool = False
    log_format: str = Field(default="console", description="console | json")
    config_dir: Path = Path("config")
    prompts_dir: Path = Path("prompts")

    # --- adapter selection (D3) ---
    telephony_provider: TelephonyProviderName = TelephonyProviderName.SIMULATED
    stt_engine: SttEngineName = SttEngineName.SCRIPTED
    stt_model: str = "biodatlab/whisper-th-medium-combined"
    stt_device: str = "auto"
    stt_compute_type: str = "int8_float16"
    llm_provider: LlmProviderName = LlmProviderName.RULEBASED
    llm_model: str = "claude-sonnet-5"
    llm_base_url: str | None = None
    tts_engine: TtsEngineName = TtsEngineName.NULL
    core_data_provider: CoreDataProviderName = CoreDataProviderName.FIXTURES
    core_mapping_file: Path = Path("config/core_mapping.yaml")
    core_fixtures_dir: Path = Path("mock/bank_core/fixtures")
    intake_strategy: IntakeStrategyName = IntakeStrategyName.PASSIVE
    event_bus: EventBusName = EventBusName.MEMORY
    blob_storage: BlobStorageName = BlobStorageName.MEMORY

    # --- domain pack (D28): insurance-specific config lives in files, not code ---
    intents_file: Path = Path("config/intents.yaml")
    skills_file: Path = Path("config/skills.yaml")
    queues_file: Path = Path("config/queues.yaml")
    queue_hours_file: Path = Path("config/queue_hours.yaml")
    dids_file: Path = Path("config/dids.yaml")
    voice_prompts_file: Path = Path("config/voice_prompts.yaml")
    matching_weights_file: Path = Path("config/matching_weights.yaml")

    # --- intake / IVR ---
    intake_max_duration_s: float = 180.0
    intake_silence_timeout_s: float = 6.0
    intake_reoffer_after_s: float = 90.0
    ivr_barge_in: bool = True

    # --- analysis ---
    analysis_debounce_s: float = 5.0
    confidence_floor: float = Field(default=0.55, ge=0.0, le=1.0)
    llm_timeout_s: float = 8.0
    brief_deadline_ms: float = 1000.0
    live_call_transcription: bool = True
    live_call_stt_engine: SttEngineName | None = None

    # --- matching (D22) ---
    matcher_tick_ms: float = 1000.0
    matcher_solver: MatcherSolverName = MatcherSolverName.HUNGARIAN
    target_wait_s: float = 45.0
    max_wait_before_any_agent_s: float = 180.0
    defer_enabled: bool = True
    defer_max_wait_s: float = 60.0
    defer_max_hold_s: float = 25.0
    defer_min_fit_gap: float = Field(default=0.25, ge=0.0, le=1.0)

    # --- identity (D20) ---
    identity_pending_intent_window_s: float = 900.0
    require_l2_for_policy_details: bool = True

    # --- the public API (P1b) ---
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_cors_origins: list[str] = Field(default_factory=list)
    #: How long a correlation token stays usable. Must not exceed the window the identity
    #: resolver will still honour a pending intent for, or the app path would mint tokens
    #: that quietly resolve to a weaker rung than the caller expects.
    intent_ttl_s: float = 900.0
    session_cookie_name: str = "readycall_session"
    session_ttl_s: float = 3600.0
    #: The number the app tells the customer to dial. Config, never hardcoded (`D28`).
    default_dial_target: str = "+6621234000"
    #: DEMO: enables /v1/demo/* - the persona picker that stands in for a real bank login.
    #: Must be false anywhere near real data. See `D47`.
    demo_login_enabled: bool = True

    # --- agent workstation (D32, D33) ---
    agent_accept_mode: AcceptMode = AcceptMode.MANUAL
    offer_timeout_s: float = 20.0
    #: After-call work thresholds are **visibility only** (`D45`). Nothing expires,
    #: auto-saves or auto-readies when they pass — ACW ends when the agent declares what
    #: they are doing next, full stop. Renamed from `acw_timer_s` / `acw_max_s`, which
    #: read like expiry deadlines and were an open invitation to implement one.
    acw_long_after_s: float = 45.0
    acw_supervisor_alert_after_s: float = 300.0
    agent_heartbeat_s: float = 10.0
    agent_presence_ttl_s: float = 30.0
    #: How often the API process runs the things that happen because **time passed**:
    #: offer expiry (RONA), re-matching the caller nobody answered, and dropping agents
    #: whose heartbeat died. Set to 0 to disable, which is what a test wants when it
    #: drives the sweep itself. `B7`: all three were written expecting this driver and it
    #: did not exist, so an unanswered offer stranded the agent in `OFFERING` for good.
    agent_sweep_interval_s: float = 1.0
    agent_session_cookie_name: str = "readycall_agent"
    agent_session_ttl_s: float = 43200.0  # a shift, not an hour
    #: DEMO: enables /v1/agent/demo-login, the staff-side equivalent of the persona
    #: picker. Must be false anywhere near real data.
    demo_agent_login_enabled: bool = True
    sip_wss_url: str | None = None
    sip_realm: str = "readycall.local"

    # --- retention (D14) ---
    recording_retention_days: int = 90
    transcript_retention_days: int = 365

    # --- infrastructure ---
    readycall_database_url: str | None = None
    core_database_url: str | None = None
    redis_url: str | None = None

    # --- secrets (never logged, never committed) ---
    anthropic_api_key: str | None = None
    llm_api_key: str | None = None

    @model_validator(mode="after")
    def _check_coherent(self) -> Settings:
        # Fail at startup on combinations that would only break mid-call.
        if self.llm_provider is LlmProviderName.OPENAI_COMPATIBLE and not self.llm_base_url:
            raise ConfigError("LLM_PROVIDER=openai_compatible requires LLM_BASE_URL")
        if self.llm_provider is LlmProviderName.ANTHROPIC and not self.anthropic_api_key:
            # A warning-level condition, not fatal: the rule-based fallback still works,
            # so a dev without a key can run everything except real LLM calls.
            pass
        if self.defer_max_wait_s < self.defer_max_hold_s:
            raise ConfigError(
                "DEFER_MAX_WAIT_S must be >= DEFER_MAX_HOLD_S, otherwise deferral can "
                "never fire and the config is lying about being enabled"
            )
        if self.max_wait_before_any_agent_s <= self.target_wait_s:
            raise ConfigError(
                "MAX_WAIT_BEFORE_ANY_AGENT_S must exceed TARGET_WAIT_S — the hard "
                "anti-starvation ceiling has to sit above the soft target (D22)"
            )
        if self.acw_long_after_s > self.acw_supervisor_alert_after_s:
            raise ConfigError(
                "ACW_LONG_AFTER_S must be <= ACW_SUPERVISOR_ALERT_AFTER_S — the agent "
                "sees their own wrap-up running long before a supervisor is told (D45)"
            )
        return self

    @property
    def effective_live_call_stt_engine(self) -> SttEngineName:
        """Live-call transcription may use a lighter model than intake (`D26`)."""
        return self.live_call_stt_engine or self.stt_engine


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def load_settings(**overrides: object) -> Settings:
    """Build settings explicitly. Used by tests and the scenario runner."""
    return Settings(**overrides)  # type: ignore[arg-type]
