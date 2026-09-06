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


class SttWorkerMode(StrEnum):
    """Whether the STT engine shares this process (`D112`).

    `inline` is right for a scripted engine and for every test; `subprocess` is what a
    decode timeout requires, because a deadline you cannot enforce is not a deadline.
    """

    INLINE = "inline"
    SUBPROCESS = "subprocess"


class VadEngineName(StrEnum):
    """Which voice detector runs. `energy` needs nothing and is the CI/degraded path."""

    ENERGY = "energy"
    SILERO = "silero"


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


class StorageBackend(StrEnum):
    """Where our writable state lives (`D75`)."""

    MEMORY = "memory"
    POSTGRES = "postgres"


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
    #: Versioned LLM prompt files (`D18`, `D119`). Separate from `config_dir` because they
    #: are model instructions rather than domain data, and because the version in each
    #: filename is stored against every result the prompt produced.
    prompt_dir: Path = Path("prompts/th")
    prompts_dir: Path = Path("prompts")

    # --- adapter selection (D3) ---
    telephony_provider: TelephonyProviderName = TelephonyProviderName.SIMULATED
    stt_engine: SttEngineName = SttEngineName.SCRIPTED
    #: `silero` needs the `ml` extra (`D95`); `energy` needs nothing and is what CI
    #: and the stage-safe path run (`D96`).
    vad_engine: VadEngineName = VadEngineName.ENERGY
    #: The checkpoint, or **blank to take whichever default the chosen adapter declares**
    #: — which is the point, because the two Thonburian engines want different things from
    #: this field. `thonburian_hf` wants the Hugging Face id; `thonburian_ct2` wants a
    #: **local CTranslate2 directory**, because Thonburian publishes no CT2 build (`B17`).
    #:
    #: It used to default to the HF id, which meant `STT_ENGINE=thonburian_ct2` on its own
    #: handed faster-whisper a transformers checkpoint it cannot read (`B23`). Blank is the
    #: only default that is correct for both.
    stt_model: str = ""
    #: `auto` resolves to cuda when a GPU is actually usable and cpu otherwise (`D96`).
    #: Resolved in `build_stt`, not here, because deciding it at import time would make
    #: Settings depend on torch.
    stt_device: str = "auto"
    #: int8 weights, fp16 compute. The default because of what `D95` measured: 4.00 GiB
    #: total and ~3.2 GiB free, against a medium checkpoint that wants most of it.
    stt_compute_type: str = "int8_float16"
    #: Where the STT engine runs (`D112`). `inline` is the default and is what every
    #: test, scenario and demo uses. `subprocess` puts it in a child process so a runaway
    #: decode can be KILLED — the preventer `D98` designed and refused to fake, because
    #: `asyncio.wait_for` around `to_thread` does not kill the thread.
    stt_worker: SttWorkerMode = SttWorkerMode.INLINE
    #: The deadline one utterance gets, enforced only when `STT_WORKER=subprocess`.
    #: 8.0 s because that is what `B14` measured a *single* second of near-silence
    #: costing on this GPU: the guard is for the pathological case, not for a slow model.
    stt_decode_timeout_s: float = 8.0
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
    #: ⚠️ The matcher does NOT read this — it reads `config/matching_weights.yaml`, where
    #: the ceiling is now a per-urgency table (`D94`). This field survives only as the
    #: bound for the coherence check below. See `Q26`: an env var that changes nothing is
    #: worse than no env var, and the honest fix is to delete it or wire it up.
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
    #: How often the API process runs the event bus's handlers. Separate from the sweep,
    #: and much faster, because it is on the transcript's latency path: `ARCHITECTURE`
    #: §15 budgets **1.5 s** from utterance end to a turn on screen and the model already
    #: spends 0.19 s of it (`D104`), so a 1 s bus latency would eat most of what is left.
    #: `publish()` only enqueues by design (`D15`) — determinism is what makes scenario
    #: replay comparable — so **something has to call `drain()`**, and until `D105` the
    #: only thing that did was a background task on `POST /v1/calls/intents`. Set to 0 to
    #: disable, which is what a test wants when it drains explicitly.
    bus_drain_interval_s: float = 0.05
    agent_session_cookie_name: str = "readycall_agent"
    agent_session_ttl_s: float = 43200.0  # a shift, not an hour
    #: DEMO: enables /v1/agent/demo-login, the staff-side equivalent of the persona
    #: picker. Must be false anywhere near real data.
    demo_agent_login_enabled: bool = True
    #: DEMO: the only directory `POST /v1/demo/calls` will play a WAV out of (`D107`).
    #: The request sends a bare filename and this says where it may live, so the endpoint
    #: is never a way to read an arbitrary file. Real audio arrives from telephony at P5
    #: and none of this exists on that path.
    demo_audio_dir: Path = Path("tests/audio")
    #: DEMO: the lines the `scripted` STT engine speaks, one per endpointed utterance
    #: (`D107`). Empty or missing means the engine returns nothing, which is what it did
    #: for its whole life and why the stage-safe fallback showed a blank panel.
    demo_transcript_file: Path = Path("config/demo_transcript.yaml")
    sip_wss_url: str | None = None
    sip_realm: str = "readycall.local"

    # --- the recording, and the keys that protect it (D14, D110) ---
    #: Whether a consented intake is written to object storage at all. Off makes the
    #: system behave exactly as it did before `D110`: audio is analysed in memory,
    #: per utterance, and never reaches a disk (`D9`).
    recording_enabled: bool = True
    #: Base64, 32 bytes. Unset means a key generated for this process only, which is
    #: fine against `memory` and refused against a durable store — see `_check_coherent`.
    recording_master_key: str | None = None
    #: Where `localfs` puts objects. Gitignored; it holds ciphertext, but ciphertext of
    #: a real customer talking is still a thing to keep out of a repository (`D97`).
    blob_root: Path = Path("var/blobs")
    blob_bucket: str = "readycall-recordings"
    #: MinIO in `infra/docker-compose.yml`. Leave unset for real AWS.
    blob_endpoint_url: str | None = "http://127.0.0.1:9000"
    blob_access_key: str | None = None
    blob_secret_key: str | None = None
    blob_region: str = "us-east-1"

    # --- retention (D14) ---
    recording_retention_days: int = 90
    transcript_retention_days: int = 365

    # --- infrastructure ---
    #: Which store backs the call sessions and the agent state log (`D75`). `memory` keeps
    #: the whole system runnable with no container, which is what every scenario replay and
    #: most of the suite uses; `postgres` is what survives a restart. The seam is the
    #: repository interface P0 already had, so this is one factory line either way.
    storage_backend: StorageBackend = StorageBackend.MEMORY
    readycall_database_url: str | None = None
    core_database_url: str | None = None
    redis_url: str | None = None

    @property
    def database_url(self) -> str:
        """Where our own tables live. Used by the engine AND by Alembic (`D75`).

        One source, because a migration run against a different database from the one the
        app opens is a failure mode that looks like "the table does not exist" and wastes
        an hour every time.
        """
        return self.readycall_database_url or (
            "postgresql+asyncpg://readycall:readycall@127.0.0.1:5432/readycall"
        )

    # --- secrets (never logged, never committed) ---
    #
    # **Declared here even where nothing reads them yet, and that is the opposite of
    # `Q26`'s complaint about dead env vars.** A behaviour knob nothing reads is a lie
    # about what the system does. A *secret* that is declared but unread is a slot: it is
    # redacted from every log line by name (`logging._SENSITIVE_KEYS`), it is documented
    # in one place, and the day its adapter lands there is nowhere new to put it. The
    # cost of the other way is somebody pasting a live key into a file that gets
    # committed because there was no obvious home for it.
    #
    # ⚠️ **Every one of these belongs in `.env`, which is gitignored. `.env.example` gets
    # the NAME and never the value.**

    #: Read today by `AnthropicAdapter` (`D29`), when `LLM_PROVIDER=anthropic`.
    anthropic_api_key: str | None = None
    #: Read today by `OpenAiCompatibleAdapter` — one adapter covers Typhoon-hosted,
    #: OpenAI, vLLM and Ollama, distinguished only by `LLM_BASE_URL` (`D29`).
    llm_api_key: str | None = None
    #: Not read yet. `GeminiAdapter` is defined in `D29` and not built.
    gemini_api_key: str | None = None
    #: Not read yet. For gated Hugging Face checkpoints — every model this project uses
    #: today is public, so nothing needs it, and a gated one would fail at load without it.
    huggingface_token: str | None = None
    #: Not read yet. P5's telephony (`INTEGRATIONS` §1.1): Asterisk ARI is user+password,
    #: Twilio is account SID + auth token.
    asterisk_ari_username: str | None = None
    asterisk_ari_password: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    #: Not read yet. P7 replaces `LocalKeyRing` with a vault (`D110`); this is where its
    #: credential will go, and `RECORDING_MASTER_KEY` above is what it will replace.
    vault_token: str | None = None

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
        if self.stt_engine is SttEngineName.THONBURIAN_CT2 and self.stt_model:
            # `thonburian_ct2` is faster-whisper, which reads a CTranslate2 DIRECTORY.
            # An HF-style id here is the `B23` mistake and fails at model load, on the
            # box with the GPU, which is the worst place and time to find out.
            looks_like_an_hf_id = "/" in self.stt_model and not Path(self.stt_model).exists()
            if looks_like_an_hf_id:
                raise ConfigError(
                    f"STT_ENGINE=thonburian_ct2 needs a local CTranslate2 directory, but "
                    f"STT_MODEL={self.stt_model!r} looks like a Hugging Face id and does "
                    f"not exist on disk. Thonburian publishes no CT2 build (`B17`) - "
                    f"convert it once:\n"
                    f"  uv run python scripts/convert_ct2.py\n"
                    f"then either leave STT_MODEL unset or point it at models/"
                    f"whisper-th-medium-combined-ct2"
                )
        if self.recording_enabled and not self.recording_master_key:
            # An ephemeral master key against a store that outlives the process writes
            # ciphertext nobody will ever read again — a recording that exists, costs
            # money, satisfies an audit on paper, and cannot be played (`D110`). Against
            # `memory` it is exactly right, because the objects die with the key.
            durable = self.blob_storage is not BlobStorageName.MEMORY
            if durable:
                raise ConfigError(
                    f"BLOB_STORAGE={self.blob_storage.value} needs RECORDING_MASTER_KEY. "
                    f"Without one the master key is generated per process, so anything "
                    f"written now is unreadable after a restart. Generate one with:\n"
                    f'  python -c "import base64,os;'
                    f'print(base64.b64encode(os.urandom(32)).decode())"\n'
                    f"or set RECORDING_ENABLED=false to keep audio out of storage entirely."
                )
        if self.recording_master_key:
            # Validated here rather than at first use: a bad key is otherwise discovered
            # by a playback weeks later, against recordings that are already unreadable.
            from readycall.adapters.keyring.local import decode_master_key

            decode_master_key(self.recording_master_key)
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
