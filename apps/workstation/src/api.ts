/**
 * The server is the source of truth; this file just talks to it.
 *
 * Two rules that shape everything here:
 *
 * 1. **Never send an `agent_id`.** It comes from the HttpOnly session cookie, which this
 *    code cannot read and does not need to. Every request is `credentials: "include"`.
 * 2. **Never derive state the server already computed.** `offerable`, `may_disclose_...`
 *    and the brief's contents are server-side decisions (`D42`); recomputing them here
 *    would let the two disagree, and the client's copy would be the wrong one.
 */

export type Presence = {
  agent_id: string;
  display_name: string;
  system_state: string;
  agent_intent: string;
  offerable: boolean;
  since: string;
  current_load: number;
  max_concurrent: number;
  skills: { skill_code: string; label_th: string; proficiency: number }[];
  acw_seconds: number | null;
  /** The anchor the client ticks its own ACW clock from (`D59`). */
  acw_since: string | null;
  long_acw: boolean;
  /** Which intents may be declared right now. The server decides (`D59`). */
  declarable: string[];
  /** True while in after-call work: a declaration is owed before going available. */
  awaiting_declaration: boolean;
  /** signed_in | rona_missed_offer | last_call_fulfilled | agent_declared (`D59`). */
  intent_reason: string;
};

export type Offer = {
  assignment_id: string;
  call_session_id: string;
  accept_mode: string;
  timeout_s: number;
  offered_at: string;
  queue_id: string;
  queue_label_th: string;
  intent_code: string | null;
  intent_label_th: string | null;
  urgency: string;
  waited_s: number;
  assurance: string;
  rationale_th: string | null;
  /** A preview of the brief, gated exactly as the brief is (`D69`). At L1 the name is
   *  null and only the reason for the call survives — there is nothing here to leak. */
  summary_th: string | null;
  customer_name_th: string | null;
  first_action_th: string | null;
};

export type Identity = {
  assurance: string;
  customer_id: string | null;
  method: string;
  /** May the agent ACT on the policy — say a number aloud, confirm a figure, change
   *  something. Not about what they can see; that is `may_see_record` (`D74`). */
  may_act_on_policy: boolean;
  /** Is there an identified customer to render at all. False only at L0. */
  may_see_record: boolean;
  authority_check_required: boolean;
  /** Once true the control locks — all three outcomes (`D60`). */
  attested: boolean;
  attested_outcome: string | null;
  third_party_name: string | null;
  relationship: string | null;
  /** Grows by one per attestation, amendments included — the signal the panel re-locks on. */
  attestation_count: number;
  /** Which of the three outcomes may be pressed right now. Server-decided (`D71`). */
  attestable: string[];
  /** The caller authenticated before the agent saw the call — changes the third-party wording. */
  system_verified: boolean;
};

export type Capture = {
  capture_id: string;
  state: string;
  length: number;
  /** The real digits — for the agent's own panel. `D44`'s inverted default is "masked in
   *  transcripts and logs", and the agent is the person the capture was made for (`D58`). */
  digits: string;
  /** What everything that is not this panel sees. */
  masked: string;
  labelled_as: string | null;
  lookups: {
    kind: string;
    matched: boolean;
    matched_value: string | null;
    detail: string | null;
  }[];
};

export type Challenge = {
  code: string;
  label_th: string;
  requires_note: boolean;
};

export type Queue = {
  queue_id: string;
  label_th: string;
  sla_seconds: number;
  is_open: boolean;
  closed_reason: string | null;
  next_open_at: string | null;
  waiting: number;
  longest_wait_s: number;
  /** Whether this agent holds the queue's required skill — i.e. whether any of these
   *  callers could actually reach them. Server-computed (`D70`). */
  mine: boolean;
};

/** Mirrors `BriefOut`. Deliberately not `Record<string, unknown>`: the point of the DTO
 *  is that a policy number cannot be in the payload below L2, and a loose type here would
 *  let a component reach for one anyway and quietly render `undefined`. */
export type Brief = {
  version: number;
  kind: string;
  urgency: string;
  intent: { code: string; label_th: string; confidence: number; source: string } | null;
  summary_th: string | null;
  suggested_opening_th: string | null;
  actions_th: string[];
  customer: { display_name_th: string; segment: string | null; is_vulnerable: boolean } | null;
  relevant_policy: {
    policy_no: string;
    product_th: string | null;
    status: string;
    line: string;
    sum_insured: number | null;
    next_due_date: string | null;
    coverages: { label_th: string; limit_text: string | null }[];
  } | null;
  other_policy_count: number;
  recent_claim_count: number;
  last_contact_th: string | null;
  disclosure_locked: boolean;
  degraded: string;
  build_ms: number | null;
  provenance: { field: string; source: string; stale: boolean }[];
} | null;

/** A call this agent handled and never filed anything for (`D87`). Derived server-side
 *  from "after-call work ended and no wrap-up exists", so filing one clears it by
 *  construction — there is no flag here that could be forgotten. */
export type PendingWrapup = {
  call_session_id: string;
  ended_at: string | null;
  acw_seconds: number | null;
  intent_code: string | null;
  intent_label_th: string | null;
  customer_name_th: string | null;
  assurance: string;
};

/** One utterance of the live transcript (`D106`).
 *
 *  Deliberately NOT gated on assurance: this is the caller's own speech on the call being
 *  taken, not anything looked up about them, and an anonymous caller at L0 is exactly the
 *  case with no other source of context. What is gated is the customer *record* — see
 *  `Brief`, where the fields simply are not present until the level allows them. */
export type TranscriptTurn = {
  turn_id: string;
  seq: number;
  /** From the leg the audio was forked from, never from a diarisation model (`D26`). */
  speaker_role: string;
  text: string;
  /** Milliseconds from the start of the recording. NOT a wall clock — do not apply the
   *  skew correction to these; they are offsets within the audio, not timestamps. */
  t_start_ms: number;
  t_end_ms: number;
  asr_confidence: number | null;
};

export type Snapshot = {
  presence: Presence;
  offer: Offer | null;
  active_call_session_id: string | null;
  identity: Identity | null;
  brief: Brief;
  captures: Capture[];
  queues: Queue[];
  /** The verification methods this deployment allows, from config (`D72`). Rendered
   *  rather than hardcoded, so the panel can never offer what the server would refuse. */
  challenges: Challenge[];
  /** The server's clock at the moment this snapshot was built. Timers subtract a
   *  server-minus-browser offset derived from it, so a drifted laptop clock does not
   *  silently make every duration on screen wrong. */
  server_time: string | null;
  /** When the current call was answered, so the call timer survives a page refresh. */
  call_answered_at: string | null;
  /** Whether the wrap-up record for the call being wrapped has been saved. Server-owned:
   *  the client used to track this locally and lost it on every refresh. */
  wrapup_saved: boolean;
  /** The call in after-call work, which outlives its own record closing (`D45`). */
  wrapup_call_session_id: string | null;
  /** Calls left without a wrap-up, oldest first (`D87`). The agent is free to walk away
   *  mid-form; this is what stops that meaning the note is lost. */
  pending_wrapups: PendingWrapup[];
  /** The live transcript of the call being handled or wrapped up, oldest first (`D106`).
   *
   *  Also pushed on the socket as `transcript`, and **every push carries the whole list**
   *  rather than the new turn — so this is replaced, never appended to. That is the point:
   *  a client that accumulates can lose a message and show a transcript with a sentence
   *  silently missing from the middle, which is `D68`'s rule in the place it matters most. */
  transcript: TranscriptTurn[];
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    // The cookie is HttpOnly, so it travels only because of this line.
    credentials: "include",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  const parsed = text ? JSON.parse(text) : null;
  if (!response.ok) {
    // FastAPI's validation errors arrive as a list of objects; a 422 rendered as
    // "[object Object]" in a toast is how you lose ten minutes to a typo.
    const detail = parsed?.detail;
    const message =
      typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : response.statusText;
    throw new ApiError(response.status, message);
  }
  return parsed as T;
}

export const api = {
  signIn: (agentId: string) =>
    call<Presence>("POST", "/v1/agent/demo-login", { agent_id: agentId }),
  signOut: () => call<void>("POST", "/v1/agent/logout"),
  me: () => call<Snapshot>("GET", "/v1/agent/me"),
  declare: (intent: string) =>
    call<Presence>("POST", "/v1/agent/state", { agent_intent: intent }),

  accept: (assignmentId: string) =>
    call<Snapshot>("POST", `/v1/agent/offers/${assignmentId}/accept`),
  decline: (assignmentId: string, reason: string) =>
    call<Snapshot>("POST", `/v1/agent/offers/${assignmentId}/decline`, { reason }),

  endCall: (callId: string, reason = "caller_hung_up") =>
    call<Snapshot>("POST", `/v1/agent/calls/${callId}/end`, { reason }),
  saveWrapup: (callId: string, payload: Record<string, unknown>) =>
    call<Snapshot>("POST", `/v1/agent/calls/${callId}/wrapup`, payload),

  attest: (callId: string, payload: Record<string, unknown>) =>
    call<Snapshot>("POST", `/v1/agent/calls/${callId}/identity`, payload),

  startCapture: (callId: string) =>
    call<Capture>("POST", `/v1/agent/calls/${callId}/capture`),
  sendKeys: (captureId: string, digits: string) =>
    call<Capture>("POST", `/v1/agent/captures/${captureId}/keys`, { digits }),
  stopCapture: (captureId: string) =>
    call<Capture>("POST", `/v1/agent/captures/${captureId}/stop`),
  backspaceCapture: (captureId: string) =>
    call<Capture>("POST", `/v1/agent/captures/${captureId}/backspace`),
  discardCapture: (captureId: string) =>
    call<Capture>("POST", `/v1/agent/captures/${captureId}/discard`),
  lookupCapture: (captureId: string, kind: string) =>
    call<Capture>("POST", `/v1/agent/captures/${captureId}/lookup`, { kind }),

  /** DEMO: a caller arrives. Stands in for telephony until P5. */
  placeCall: (payload: Record<string, unknown>) =>
    call<Record<string, unknown>>("POST", "/v1/demo/calls", payload),
  roster: () => call<{ agent_id: string; display_name: string; team: string }[]>(
    "GET",
    "/v1/demo/agents",
  ),
};
