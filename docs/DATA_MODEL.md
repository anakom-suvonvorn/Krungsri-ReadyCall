# DATA_MODEL

_The two databases, every table, and — most importantly — how the bank's half gets swapped out for the real thing on hackathon day._
_Status: **design only**. Last updated: 2026-08-17._

---

## 1. Two stores, one hard boundary

| | `core` (simulated bank data) | `readycall` (ours) |
|---|---|---|
| Represents | What Krungsri already has: KYC, policies, claims, holdings, interactions | Everything ReadyCall produces |
| Access | **READ ONLY**, through the `CoreDataProvider` port | Read/write, normal ORM |
| Owner | The bank (on hackathon day: whatever they hand us) | Us |
| DB role | `readycall_ro` — `SELECT` grant only, enforced at the DB | `readycall_rw` |
| Replaceable | **Yes — that's the point** (§4) | No |

The split is deliberate and matches reality: a hackathon almost certainly hands us **read-only**
extracts (CSV/JSON/an API), with no way to write back. Building on that assumption from day one means
hackathon-day integration is a config change, not a refactor. (`D5`)

Anything we generate — transcripts, briefs, routing decisions, wrap-ups — lives in `readycall` and is
joined to core rows by key (`customer_id`, `policy_no`) at read time.

---

## 2. `core` — the simulated bank/insurer data

Modelled directly on the brief's *"DATA AVAILABLE TO BROKER / BANK"* list. Postgres schema `core`,
seeded by `mock/bank_core/generate.py`, exposed only via the port.

### Identity & KYC
| Table | Key columns |
|---|---|
| `customers` | `customer_id` PK, `citizen_id_hash`, `title`, `first_name_th/en`, `last_name_th/en`, `dob`, `gender`, `marital_status`, `dependants`, `occupation`, `employment_type`, `income_band`, `segment` (salaried / SME owner / freelancer — **the brief's 3 personas**), `tier`, `preferred_language`, `email`, `address_province`, `kyc_status`, `kyc_updated_at`, `is_vulnerable`, `created_at` |
| `customer_phones` | `phone_e164` PK, `customer_id`, `type`, `is_primary`, `verified_at` — **the ANI → customer lookup for the PSTN path** |
| `customer_consents_master` | bank-level consent flags we must respect (marketing, analytics, health-data, cross-org sharing) |

### Insurance
| Table | Key columns |
|---|---|
| `products` | `product_code` PK, `line` (health/life/motor/travel/pa/savings), `name_th/en`, `short_desc`, `target_segment`, `features_json`, `is_active` |
| `policies` | `policy_no` PK, `customer_id`, `product_code`, `status` (active/lapsed/pending/cancelled), `effective_date`, `expiry_date`, `sum_insured`, `premium`, `payment_frequency`, `next_due_date`, `coverage_json` (IPD room & board, OPD, deductible, co-pay, exclusions…), `riders_json`, `beneficiaries_json`, `channel_sold`, `agent_id_of_record` |
| `claims` | `claim_id` PK, `policy_no`, `type`, `status`, `submitted_at`, `incident_date`, `amount_claimed`, `amount_paid`, `hospital_name`, `documents_required_json`, `last_update_at` |
| `policy_documents` | `doc_id`, `policy_no`, `kind`, `url_ref` (metadata only, no binaries) |

### Wider financial picture
| Table | Key columns |
|---|---|
| `financial_products` | deposits / loans / cards / funds holdings: `holding_id`, `customer_id`, `kind`, `opened_at`, `balance_band`, `status` |
| `transaction_features` | **derived, not raw** — `customer_id`, `month`, `inflow_band`, `outflow_band`, `top_spend_categories`, `income_stability_score`. Privacy by construction: raw transactions are never exposed to the AI layer. |
| `life_events` | `event_id`, `customer_id`, `signal` (mortgage / new_child / job_change / relocation), `detected_at`, `confidence`, `source` |

### Interaction history (the "recent context" on the agent screen)
| Table | Key columns |
|---|---|
| `interactions` | `interaction_id`, `customer_id`, `channel` (call/chat/branch/email/app), `direction`, `occurred_at`, `topic`, `summary`, `agent_id`, `outcome`, `duration_s` |
| `app_usage_events` | `event_id`, `customer_id`, `occurred_at`, `screen`, `product_code`, `action`, `dwell_ms` — *(the bank's own telemetry; ours is separate, see §3)* |
| `message_engagement` | campaign sends/opens/clicks |

**Note on agents:** `agents` / skills / presence live in **our** store (§3), because ReadyCall owns
routing. If the bank supplies a real agent roster, it arrives through an `AgentDirectoryProvider`
port and is mirrored, not replaced.

---

## 3. `readycall` — our writable store

### Call lifecycle
| Table | Key columns |
|---|---|
| `call_intents` | `intent_id` PK, `customer_id`, `product_code`, `plan_id`, `entry_screen`, `app_context_json`, `correlation_token` (hashed), `channel`, `status`, `created_at`, `expires_at` |
| `call_sessions` | `call_session_id` PK, `intent_id?`, `customer_id?`, `telephony_call_id`, `provider`, `direction`, `state`, `queue_id`, `priority`, `started_at`, `queued_at`, `answered_at`, `ended_at`, `end_reason`, `stage_timings_json` |
| `call_state_transitions` | `id`, `call_session_id`, `from_state`, `to_state`, `reason`, `at` — the demonstrable timeline |
| `app_context_events` | our own in-app telemetry: `customer_id`, `occurred_at`, `screen`, `product_code`, `section`, `dwell_ms`, TTL-pruned |

### Context
| Table | Key columns |
|---|---|
| `context_snapshots` | `snapshot_id`, `call_session_id?`, `intent_id?`, `customer_id`, `built_at`, `payload_json` (the frozen `Customer360`), `provenance_json` (field → source + fetched_at), `freshness_s`, `provider_name`, `is_stale` |

Freezing the snapshot matters: the agent screen must show *what the system knew when it decided*,
and a demo must be reproducible even if the upstream source changes.

### Consent & privacy
| Table | Key columns |
|---|---|
| `consents` | `consent_id`, `customer_id`, `call_session_id?`, `scope` (recording / ai_processing / health_data / cross_org), `granted`, `basis`, `channel`, `granted_at`, `expires_at`, `evidence_ref` |
| `pii_spans` | `id`, `transcript_turn_id`, `start`, `end`, `kind`, `action` (mask/redact/allow) |
| `audit_log` | `id`, `actor` (agent/service), `action`, `subject_customer_id`, `resource`, `at`, `justification`, `request_id` |
| `retention_jobs` | artifact kind, policy, last run, rows purged |

### Intake, audio, transcript
| Table | Key columns |
|---|---|
| `intake_sessions` | `intake_id`, `call_session_id`, `strategy` (passive/guided/conversational), `started_at`, `ended_at`, `finalize_reason` (customer_done / queue_pop / timeout / error), `is_partial`, `slots_json` |
| `audio_recordings` | `recording_id`, `call_session_id`, `phase` (intake/live_call), `storage_ref`, `format`, `sample_rate`, `duration_s`, `checksum`, `encryption_key_ref`, `delete_after` |
| `transcript_turns` | `turn_id`, `call_session_id`, `intake_id?`, `seq`, `speaker_role` (customer/ai/agent), `text`, `t_start_ms`, `t_end_ms`, `asr_confidence`, `engine`, `engine_version`, `is_final`, `created_at` |

Turns are written **incrementally**, so an abandoned call still leaves everything captured so far.

### AI outputs
| Table | Key columns |
|---|---|
| `analyses` | `analysis_id`, `call_session_id`, `kind` (intent/entities/sentiment/summary/nba/opening), `version`, `input_ref`, `output_json`, `model`, `prompt_version`, `latency_ms`, `token_in/out`, `cost`, `created_at` |
| `case_briefs` | `brief_id`, `call_session_id`, `version`, `is_partial`, `intent_code`, `intent_label_th/en`, `confidence`, `summary_th`, `entities_json`, `recommended_actions_json`, `next_best_action`, `suggested_opening_th`, `built_at`, `sources_json` |
| `brief_feedback` | `id`, `brief_id`, `agent_id`, `rating`, `wrong_fields_json`, `comment`, `at` — the evaluation signal |

Briefs are **versioned, never mutated**: v1 may be context-only, v2 after the first utterances, v3
final. The screen renders the latest; the history is what lets us measure how early we got it right.

### Routing & agents
| Table | Key columns |
|---|---|
| `agents` | `agent_id`, `display_name`, `team`, `level`, `languages`, `licence_flags`, `max_concurrent`, `is_active` |
| `agent_skills` | `agent_id`, `skill_code` (e.g. `health.ipd`, `motor.claim`), `proficiency` 0–1, `certified_until` |
| `agent_presence` | `agent_id`, `state` (available/on_call/wrap/away/offline), `since`, `current_load`, `last_assigned_at` |
| `queues` | `queue_id`, `name`, `required_skill`, `sla_seconds`, `overflow_queue_id`, `priority_rules_json` |
| `queue_entries` | `id`, `queue_id`, `call_session_id`, `enqueued_at`, `priority`, `position`, `dequeued_at`, `outcome` |
| `routing_decisions` | `decision_id`, `call_session_id`, `at`, `candidates_json` (every agent + every score term), `chosen_agent_id`, `total_score`, `rationale_th/en`, `weights_version`, `fallback_used` |
| `assignments` | `assignment_id`, `call_session_id`, `agent_id`, `offered_at`, `accepted_at`, `rejected_reason`, `ended_at` |

`routing_decisions.candidates_json` is deliberately fat — "why this agent" must be answerable months
later, for both the judges and a real auditor.

### Post-call
| Table | Key columns |
|---|---|
| `call_wrapups` | `wrapup_id`, `call_session_id`, `agent_id`, `disposition`, `summary_th` (AI draft), `summary_final` (human-confirmed), `was_edited`, `resolved`, `at` |
| `follow_up_tasks` | `task_id`, `customer_id`, `call_session_id`, `kind`, `due_at`, `assignee`, `status` |
| `metrics_rollups` | period, queue, agent, `aht_s`, `fcr_rate`, `time_to_context_ms`, `brief_ready_rate`, `abandon_rate`, `intent_accuracy` |
| `nps_responses` | `call_session_id`, `score`, `comment`, `at` |

### Ops
`settings` (runtime tunables — routing weights, thresholds, timeouts, model choice), `feature_flags`,
`service_accounts`, `prompt_versions`, `eval_runs`.

**Runtime-tunable, not hardcoded:** routing weights, the confidence floor, intake timeouts, debounce
intervals, the STT/LLM model choice, and the degradation thresholds. Demo-day tuning must never
require a code change.

---

## 4. Swapping the bank's half out (the whole point)

### The port
```python
class CoreDataProvider(Protocol):
    async def get_customer(self, customer_id: str) -> Customer | None: ...
    async def find_customer_by_phone(self, phone_e164: str) -> Customer | None: ...
    async def list_policies(self, customer_id: str, *, active_only: bool = True) -> list[Policy]: ...
    async def get_policy(self, policy_no: str) -> Policy | None: ...
    async def list_claims(self, policy_no: str) -> list[Claim]: ...
    async def list_interactions(self, customer_id: str, *, limit: int = 20) -> list[Interaction]: ...
    async def list_holdings(self, customer_id: str) -> list[Holding]: ...
    async def list_life_events(self, customer_id: str) -> list[LifeEvent]: ...
    async def get_product(self, product_code: str) -> Product | None: ...
```

It returns **domain objects**, never rows. Every adapter is responsible for its own mapping, so the
rest of the system is blind to the upstream shape.

### Planned adapters
| Adapter | For |
|---|---|
| `MockPostgresProvider` | Our seeded `core` schema. The default in dev. |
| `FixtureFileProvider` | CSV/JSON/Excel dropped in a folder — **the most likely hackathon-day shape**. |
| `HttpApiProvider` | A REST/GraphQL endpoint they expose. |
| `SqlPassthroughProvider` | A live SQL DB with an unknown schema, driven by the mapping file below. |
| `CachingProvider` | Decorator: TTL cache + stale-while-revalidate + circuit breaker. Wraps any of the above. |
| `NullProvider` | Everything unavailable — exercises the degradation ladder. |

### The field-mapping file
Re-mapping unknown data must not need code. `config/core_mapping.yaml`:

```yaml
customer:
  source: customers.csv          # or table name / endpoint path
  fields:
    customer_id:   { from: CUST_ID }
    first_name_th: { from: FNAME_TH }
    dob:           { from: BIRTH_DT, parse: "%d/%m/%Y", calendar: buddhist }
    segment:       { from: OCCUPATION_CD, map: {01: salaried, 02: sme_owner, 03: freelancer} }
    income_band:   { from: INCOME, bucket: [15000, 30000, 50000, 100000] }
policy:
  source: policies.csv
  fields:
    policy_no:     { from: POL_NO }
    coverage_json: { from: [ROOM_BOARD, OPD_LIMIT, DEDUCT], shape: coverage_v1 }
```

Thai-specific transforms that *will* come up: **Buddhist-era dates**, Thai/English name pairs,
`เลขบัตรประชาชน` formats, `+66` vs `0` phone normalisation, and Thai currency/number strings.

### The contract test suite
`tests/contracts/test_core_data_provider.py` runs the **same** suite against every adapter:
required fields present and typed, missing customer → `None` (not an exception), phone lookup
normalises `08x` ↔ `+668x`, active-only filtering, ordering of interactions, latency budget,
timeout/failure behaviour. **A hackathon-day adapter is "done" when that suite passes** — that is the
integration checklist, and it takes minutes instead of an afternoon.

### Hackathon-day playbook
1. Look at what they give us → pick the closest adapter.
2. Write/point `core_mapping.yaml` at it.
3. Run the contract suite → fix mapping until green.
4. Flip `CORE_DATA_PROVIDER=` in `.env`. Nothing else changes.
5. Keep `MockPostgresProvider` runnable as the offline fallback for the live demo — **never depend on
   their network during a stage demo.**

---

## 5. Seed / mock data plan

`mock/bank_core/generate.py` produces a deterministic (seeded) dataset:

- **~2,000 customers** across the brief's three personas (salaried employee, SME owner, freelancer)
  with realistic Thai names, ages, income bands, and provinces.
- Policy portfolios that reproduce the brief's core finding: a minority hold several policies, a
  large group holds one or none → the **coverage/life-stage mismatch** is visible in the data.
- 12–24 months of interactions, app usage, claims, and life-event signals with plausible temporal
  correlation (mortgage → life insurance interest; new child → health).
- **~15 hand-authored persona + scenario pairs** used everywhere: demos, tests, golden-set evaluation.
  Each pairs a customer with a tapped plan, an intake script (Thai text + optional recorded audio),
  the expected intent, expected routing target, and the expected brief. The pitch's own example —
  *Khun Pattheera, Health Plan A, hospitalisation tomorrow, room & board question* — is scenario #1.

Generated data is **not committed** (only the generator + the hand-authored scenario YAMLs are), so
the repo stays clean and the dataset is reproducible from a seed.
