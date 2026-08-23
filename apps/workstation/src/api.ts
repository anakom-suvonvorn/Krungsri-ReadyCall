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
  long_acw: boolean;
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
};

export type Identity = {
  assurance: string;
  customer_id: string | null;
  method: string;
  may_disclose_policy_details: boolean;
  authority_check_required: boolean;
};

export type Capture = {
  capture_id: string;
  state: string;
  length: number;
  masked: string;
  labelled_as: string | null;
  lookups: {
    kind: string;
    matched: boolean;
    matched_value: string | null;
    detail: string | null;
  }[];
  /** Only ever present on the live socket push, never on a REST body (`D44`). */
  digits?: string;
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

export type Snapshot = {
  presence: Presence;
  offer: Offer | null;
  active_call_session_id: string | null;
  identity: Identity | null;
  brief: Brief;
  captures: Capture[];
  queues: Queue[];
  server_time: string | null;
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
