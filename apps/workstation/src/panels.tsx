/**
 * The panels. Presentational: they render what the server said and call back on click.
 *
 * Two rules, and both were broken once:
 *
 * 1. **Nothing here decides what may be shown.** The brief arrives already gated for the
 *    current assurance level (`D42`, `D53`), so a locked field is *absent from the
 *    payload*. If you write `assurance >= L2 ? show : hide` in this file, the gate has
 *    moved to the wrong side of the wire — and that exact bug shipped (`B5`).
 * 2. **Nothing here decides what may be pressed.** `presence.declarable` comes from the
 *    server (`D59`). A control that cannot be used is rendered `disabled` so it *looks*
 *    unusable — refusing the click silently reads as a broken button.
 */

import { useEffect, useRef, useState } from "react";
import type {
  Brief,
  BriefContext,
  Capture,
  Challenge,
  Handoff,
  Identity,
  Offer,
  PendingWrapup,
  Presence,
  Queue,
  TranscriptTurn,
} from "./api";

const INTENT_LABEL: Record<string, string> = {
  not_ready: "ยังไม่พร้อม",
  ready: "พร้อมรับสาย",
  break: "พัก",
  lunch: "พักกลางวัน",
  training: "อบรม",
  admin: "งานเอกสาร",
  last_call: "สายสุดท้าย",
  draining: "ไม่รับสายใหม่",
};

const SYSTEM_LABEL: Record<string, string> = {
  offline: "ออกจากระบบ",
  available: "ว่าง",
  offering: "กำลังเสนอสาย",
  on_call: "อยู่ระหว่างสนทนา",
  after_call_work: "งานหลังจบสาย",
};

const URGENCY_LABEL: Record<string, string> = {
  low: "ไม่เร่งด่วน",
  normal: "ปกติ",
  high: "เร่งด่วน",
  critical: "ฉุกเฉิน",
};

const ASSURANCE_LABEL: Record<string, { text: string; tone: string }> = {
  l0_anonymous: { text: "L0 · ไม่ทราบตัวตน", tone: "bad" },
  l1_probable: { text: "L1 · น่าจะใช่ (ยังไม่ยืนยัน)", tone: "warn" },
  l2_strong: { text: "L2 · มั่นใจสูง", tone: "info" },
  l3_verified: { text: "L3 · ยืนยันตัวตนแล้ว", tone: "ok" },
};

/** `not_ready` is reached three different ways and each wants a different sentence
 *  (`D59`). Without `intent_reason` the screen cannot tell them apart. */
const REASON_HINT: Record<string, string> = {
  signed_in: "คุณเข้าสู่ระบบแล้วแต่ยังไม่ได้กด “พร้อมรับสาย” — ระบบจะไม่ส่งสายให้จนกว่าคุณจะกดเอง",
  rona_missed_offer:
    "มีสายที่เสนอให้แล้วไม่มีการตอบรับ ระบบจึงหยุดส่งสายชั่วคราว — กดสถานะใหม่เมื่อพร้อม",
  last_call_fulfilled: "สายสุดท้ายของคุณจบแล้ว — เลือกสถานะถัดไปเองเมื่อพร้อม",
  declined_and_stopped: "คุณเลือกไม่รับสายนั้นและให้หยุดส่งสายไว้ก่อน — กดสถานะใหม่เมื่อพร้อม",
};

const LOOKUP_LABEL: Record<string, string> = {
  policy_number: "เลขกรมธรรม์",
  claim_number: "เลขเคลม",
  date_of_birth: "วันเกิด",
  citizen_id_last4: "เลขบัตรประชาชน",
};

const OUTCOME_LABEL: Record<string, string> = {
  confirmed: "ยืนยันแล้วว่าเป็นเจ้าของกรมธรรม์",
  third_party: "ผู้ดำเนินการแทน (ตรวจสอบสิทธิ์แล้ว)",
  not_this_person: "ไม่ใช่บุคคลนี้",
};

export function mmss(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * A once-per-second re-render, for timers only.
 *
 * Every timer on this screen is drawn from a **server timestamp** plus the browser clock,
 * never from a counter the client increments. That is why the call timer now survives a
 * refresh: it is `now - call_answered_at`, not "seconds since this component mounted".
 */
export function useSecondTicker(active: boolean): number {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setTick((n) => n + 1), 250);
    return () => window.clearInterval(id);
  }, [active]);
  return Date.now();
}

/**
 * Seconds since a **server** timestamp, corrected for a browser clock that disagrees.
 *
 * `now - Date.parse(server_iso)` was silently mixing two clocks. On one machine they
 * agree and it is invisible; on a demo laptop whose clock has drifted, or a second machine
 * with a bad NTP sync, every duration on screen is wrong by the offset and nothing points
 * at the clock. `server_time` rides in every snapshot for exactly this and was read by
 * nothing (`D68`); `skewMs` is `server - browser` measured when the snapshot arrived.
 */
export function elapsedSince(
  iso: string | null | undefined,
  now: number,
  skewMs = 0,
): number | null {
  if (!iso) return null;
  const started = Date.parse(iso);
  return Number.isNaN(started) ? null : (now + skewMs - started) / 1000;
}

/** `server_time - browser_now`, in ms. Zero when the server did not say. */
export function clockSkewMs(serverTime: string | null | undefined): number {
  if (!serverTime) return 0;
  const server = Date.parse(serverTime);
  return Number.isNaN(server) ? 0 : server - Date.now();
}

// --- the offer card -------------------------------------------------------

export function OfferCard({
  offer,
  onAccept,
  onDecline,
  busy,
  skewMs = 0,
}: {
  offer: Offer | null;
  onAccept: () => void;
  onDecline: (reason: string, stopOffering: boolean) => void;
  busy: boolean;
  /** `server - browser`, sampled once per snapshot (`B8`). This card was the one timer
   *  that never applied it, so on a laptop with a drifted clock the countdown was wrong
   *  by the offset and nothing on screen pointed at the clock. */
  skewMs?: number;
}) {
  const now = useSecondTicker(offer !== null);
  if (!offer) return <div className="scrim hidden" />;

  const left = Math.max(0, offer.timeout_s - (elapsedSince(offer.offered_at, now, skewMs) ?? 0));
  // Ticked from the anchor, not read from the number (`B27`).
  const waited = elapsedSince(offer.waited_since, now, skewMs) ?? offer.waited_s;
  // At zero the server has already timed the offer out (RONA) and a `offer_revoked` push
  // is on its way. Hiding the card immediately rather than waiting for it stops the agent
  // from pressing Accept on a call that has already gone to somebody else — the click
  // would fail, and a failed Accept looks like the system dropped a caller.
  if (left <= 0) return <div className="scrim hidden" />;

  return (
    <div className="scrim">
      <div className="offer-card">
        <div className="row">
          <div>
            <h1>สายเข้า</h1>
            <div className="muted">
              {offer.queue_label_th} · {offer.intent_label_th ?? offer.intent_code ?? "ไม่ระบุ"}
            </div>
          </div>
          <div className="spacer" />
          <div className="countdown">{Math.ceil(left)}</div>
        </div>

        <div className="row" style={{ marginTop: 10 }}>
          <AssuranceBadge assurance={offer.assurance} />
          <span
            className={`badge ${
              offer.urgency === "critical" ? "bad" : offer.urgency === "high" ? "warn" : ""
            }`}
          >
            {URGENCY_LABEL[offer.urgency] ?? offer.urgency}
          </span>
          <span className="badge">รอมาแล้ว {mmss(waited)}</span>
        </div>

        {/* What the call is ABOUT, not only why it came here (`D69`). Server-gated: at
            L1 the name is absent from the payload, so there is nothing to hide here. */}
        {(offer.customer_name_th || offer.summary_th) && (
          <div className="rationale" style={{ borderLeftColor: "var(--info)" }}>
            {offer.customer_name_th && <div><strong>{offer.customer_name_th}</strong></div>}
            {offer.summary_th && <div>{offer.summary_th}</div>}
          </div>
        )}

        {offer.first_action_th && (
          <div className="faint" style={{ marginTop: 8 }}>
            เริ่มจาก: {offer.first_action_th}
          </div>
        )}

        {offer.rationale_th && (
          <div className="rationale">
            <div className="faint">ทำไมถึงเป็นคุณ</div>
            <div>{offer.rationale_th}</div>
          </div>
        )}

        {/* The caller has been round the whole floor and come back (`D113`). Said
            plainly, with the count, because an agent seeing the same call twice with no
            explanation concludes the system is broken — and because naming it is the
            sentence that makes somebody take it. */}
        {offer.offer_round > 1 && (
          <div className="rationale" style={{ borderLeftColor: "var(--warn)" }}>
            <div>
              สายนี้ถูกส่งต่อครบทุกคนแล้ว และวนกลับมาอีกครั้ง (รอบที่ {offer.offer_round})
            </div>
          </div>
        )}

        {/* And when there is nobody else, say so (`D113`). NOT a count of how many others
            could take it: that reads as "somebody else will" on a card whose other button
            is decline. This one is the opposite, and it is the fact the agent cannot work
            out for themselves — if they decline, it comes straight back to them. */}
        {offer.sole_candidate && (
          <div className="rationale" style={{ borderLeftColor: "var(--warn)" }}>
            <div>ขณะนี้คุณเป็นเจ้าหน้าที่คนเดียวที่รับสายนี้ได้</div>
          </div>
        )}

        <div className="row" style={{ marginTop: 14 }}>
          <button className="primary" onClick={onAccept} disabled={busy}>
            รับสาย (Accept)
          </button>
          <button className="ghost" onClick={() => onDecline("busy", false)} disabled={busy}>
            ไม่รับ (Decline)
          </button>
          {/* The "not now" ending (`D109`). Declining alone leaves the agent ready and
              the next tick can ring them again a second later — right when they turned
              down THIS caller, wrong when they meant "stop for a moment". Letting the
              card time out already does exactly this; the button is the same ending
              without twenty seconds of a ringing desk. */}
          <button
            className="ghost"
            onClick={() => onDecline("busy", true)}
            disabled={busy}
            title="ไม่รับสายนี้ และหยุดส่งสายใหม่จนกว่าคุณจะกดพร้อมอีกครั้ง"
          >
            ไม่รับ + พักรับสาย
          </button>
          <div className="spacer" />
          <span className="faint">{offer.accept_mode}</span>
        </div>
      </div>
    </div>
  );
}

export function AssuranceBadge({ assurance }: { assurance: string }) {
  const meta = ASSURANCE_LABEL[assurance] ?? { text: assurance, tone: "" };
  return <span className={`badge assurance ${meta.tone}`}>{meta.text}</span>;
}

// --- presence -------------------------------------------------------------

export function PresencePanel({
  presence,
  onDeclare,
  busy,
}: {
  presence: Presence;
  onDeclare: (intent: string) => void;
  busy: boolean;
}) {
  const allowed = new Set(presence.declarable);
  // While in after-call work the standing instruction is unchanged underneath, but the
  // agent owes us a declaration (`D45`). Showing the old choice still highlighted is what
  // made this control look broken after saving a wrap-up: "I'm marked Ready, so why am I
  // not getting calls?" — because Ready is what they said BEFORE the call.
  const showActive = !presence.awaiting_declaration;

  return (
    <div className="panel">
      <h2>สถานะของฉัน</h2>
      <div className="row">
        <strong>{presence.display_name}</strong>
        <span className="faint mono">{presence.agent_id}</span>
      </div>
      <div className="row" style={{ marginTop: 6 }}>
        <span className={`badge ${presence.offerable ? "ok" : "warn"}`}>
          <i className="dot" />
          {SYSTEM_LABEL[presence.system_state] ?? presence.system_state}
        </span>
        <span className="badge">
          {INTENT_LABEL[presence.agent_intent] ?? presence.agent_intent}
        </span>
      </div>

      {presence.awaiting_declaration ? (
        <p className="faint" style={{ marginTop: 8 }}>
          สายจบแล้ว — เลือกสถานะถัดไปเพื่อจบงานหลังสาย ระบบจะไม่ตั้งให้เอง
          <br />
          {REASON_HINT[presence.intent_reason] ??
            `(ก่อนรับสายคุณตั้งไว้เป็น “${INTENT_LABEL[presence.agent_intent] ?? presence.agent_intent}”)`}
        </p>
      ) : presence.agent_intent === "not_ready" ? (
        <p className="faint" style={{ marginTop: 8 }}>
          {REASON_HINT[presence.intent_reason] ??
            "คุณเข้าสู่ระบบแล้วแต่ยังไม่ได้กด “พร้อมรับสาย” — ระบบจะไม่ส่งสายให้จนกว่าคุณจะกดเอง"}
        </p>
      ) : null}

      <div className="row" style={{ marginTop: 10 }}>
        {Object.entries(INTENT_LABEL)
          .filter(([value]) => value !== "not_ready")
          .map(([value, label]) => (
            <button
              key={value}
              className={showActive && presence.agent_intent === value ? "on" : ""}
              onClick={() => onDeclare(value)}
              disabled={busy || !allowed.has(value)}
              title={
                allowed.has(value)
                  ? undefined
                  : "เลือกไม่ได้ขณะนี้ — ระหว่างสนทนาเปลี่ยนได้เฉพาะคำสั่งล่วงหน้า"
              }
            >
              {label}
            </button>
          ))}
      </div>
      {presence.system_state === "on_call" && (
        <div className="hint">
          ระหว่างสนทนาเปลี่ยนได้เฉพาะ พร้อมรับสาย / สายสุดท้าย / ไม่รับสายใหม่
        </div>
      )}

      <div style={{ marginTop: 12 }}>
        <div className="faint">ทักษะ</div>
        {presence.skills.map((skill) => (
          <div key={skill.skill_code} className="row" style={{ fontSize: 13 }}>
            <span>{skill.label_th}</span>
            <div className="spacer" />
            <span className="mono muted">{Math.round(skill.proficiency * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// --- identity (D42, D57, D60) ---------------------------------------------

export function IdentityPanel({
  identity,
  challenges,
  callId,
  onAttest,
  busy,
}: {
  identity: Identity | null;
  /** From `config/challenges.yaml` via the snapshot (`D72`) — never a local copy. The
   *  server refuses any code not in this list, so a hardcoded array here would eventually
   *  offer an option that fails on submit, which the agent cannot diagnose. */
  challenges: Challenge[];
  callId: string | null;
  onAttest: (payload: Record<string, unknown>) => void;
  busy: boolean;
}) {
  const [challenge, setChallenge] = useState("");
  const [challengeNote, setChallengeNote] = useState("");
  const [callerName, setCallerName] = useState("");
  const [relationship, setRelationship] = useState("");
  const [reopened, setReopened] = useState(false);

  // Re-lock as soon as a new attestation lands. `reopened` is local, so without a
  // server-side signal to reset on it stayed true for the rest of the call: one press of
  // *amend* and the three outcomes could be cycled freely, silently appending a row to the
  // disclosure log each time. `attestation_count` only ever grows (amending appends,
  // `D60`), which makes it exactly the edge to listen for.
  const attestationCount = identity?.attestation_count ?? 0;
  useEffect(() => {
    setReopened(false);
  }, [attestationCount]);

  if (!identity) {
    return (
      <div className="panel">
        <h2>ตัวตนผู้ติดต่อ</h2>
        <p className="faint">ยังไม่มีสายที่กำลังสนทนา</p>
      </div>
    );
  }

  // An attestation is a signed statement in a disclosure log, not a toggle (`D60`).
  const locked = identity.attested && !reopened;
  const disabled = busy || !callId || locked;
  // Which challenge needs an explanation is config, not a client guess (`D72`).
  const selected = challenges.find((c) => c.code === challenge) ?? challenges[0];
  const needNote = Boolean(selected?.requires_note) && !challengeNote.trim();
  const thirdPartyReady = callerName.trim().length > 0 && relationship.trim().length > 0;
  // After *ไม่ใช่บุคคลนี้* there is no proposed customer left to confirm or to act for, so
  // the server refuses both with a 400 (`attestation.py`). Customer search is not built
  // yet (`Q18`), so this is a real dead end for the rest of the call — and it must LOOK
  // like one. A live-looking button that always errors reads as a broken screen.
  // WHICH outcomes are legal comes from the server (`D71`), like `declarable` does — the
  // client renders permissions, it never computes them. Three cases behind this list:
  // nobody was ever proposed (nothing to assert about nobody); the call arrived already
  // authenticated on an app token (only the third-party question is still open); or
  // everything is available, **including after a rejection**, because a rejection is not a
  // one-way door.
  const can = (outcome: string) => identity.attestable.includes(outcome);
  const lockedHint =
    identity.attestable.length === 0
      ? "ยังไม่ทราบว่าเป็นใคร — ไม่มีข้อมูลให้ยืนยันหรือปฏิเสธ ต้องค้นหาลูกค้าก่อน (ยังไม่มีในระบบ)"
      : identity.system_verified
        ? "ยืนยันตัวตนผ่านแอปแล้ว — ไม่ต้องยืนยันซ้ำ"
        : undefined;

  return (
    <div className="panel">
      <h2>ตัวตนผู้ติดต่อ</h2>
      <AssuranceBadge assurance={identity.assurance} />
      <div className="faint" style={{ marginTop: 6 }}>
        {identity.may_act_on_policy
          ? "ยืนยันตัวตนแล้ว — อ้างอิง/ดำเนินการกับกรมธรรม์ได้"
          : identity.may_see_record
            ? "ดูข้อมูลได้ แต่ยังไม่ยืนยันตัวตน — ยังบอกเลข ยืนยันตัวเลข หรือแก้ไขข้อมูลกับผู้ติดต่อไม่ได้"
            : "ยังไม่ทราบว่าเป็นใคร"}
      </div>

      {/* The spoken half of action 0. Never a leading question: naming the customer first
          both tells whoever holds the phone that the number is theirs, and is weaker
          verification — anyone can say "yes" (`D55`). */}
      {!identity.attested && (
        <div className="rationale" style={{ borderLeftColor: "var(--info)" }}>
          <div className="faint">ถามแบบปลายเปิด อย่าถามนำ</div>
          <div>“ขอทราบชื่อผู้ติดต่อด้วยค่ะ”</div>
        </div>
      )}

      {identity.attested && (
        <div
          className={`attested ${
            identity.attested_outcome === "confirmed"
              ? ""
              : identity.attested_outcome === "third_party"
                ? "locked-warn"
                : "locked-bad"
          }`}
        >
          <div className="faint">บันทึกไว้แล้ว</div>
          <div>{OUTCOME_LABEL[identity.attested_outcome ?? ""] ?? identity.attested_outcome}</div>
          {identity.third_party_name && (
            <div className="faint">
              {identity.third_party_name} · {identity.relationship}
            </div>
          )}
          {attestationCount > 1 && (
            <div className="faint">แก้ไขแล้ว {attestationCount - 1} ครั้ง · เก็บทุกรายการไว้</div>
          )}
          {/* Always offered once anything has been attested — including after a rejection.
              An agent who pressed the wrong button must never be trapped by it; the
              correction appends, so both statements survive (`D60`). */}
          {!reopened && (
            <button className="ghost" style={{ marginTop: 8 }} onClick={() => setReopened(true)}>
              แก้ไขการยืนยัน
            </button>
          )}
          {reopened && (
            <div className="hint">
              การแก้ไขจะถูกบันทึกเพิ่มในประวัติ ไม่ได้ลบรายการเดิม
            </div>
          )}
        </div>
      )}

      {/* Driven by evidence the server clears when an amendment changes the outcome, so
          it no longer says "acting on behalf" about a call just confirmed as the
          policyholder themself (`D71`). */}
      {identity.authority_check_required && identity.attested_outcome === "third_party" && (
        <div className="rationale" style={{ borderLeftColor: "var(--warn)" }}>
          ผู้ติดต่อดำเนินการแทนเจ้าของกรมธรรม์ (เจ้าหน้าที่ตรวจสอบสิทธิ์แล้ว) —
          บันทึกทุกอย่างในนามผู้ดำเนินการแทน ไม่ใช่เจ้าของกรมธรรม์
        </div>
      )}

      {/* Three outcomes, three buttons, one click each (`D42`, drawn in
          identity_promotion.mmd). Third party is NOT "third party, then confirm" — it is
          its own path, and forcing it through Confirm would write into the audit log that
          the policyholder was verified when they were not. */}
      <div className="stack" style={{ marginTop: 12 }}>
        <div>
          <div className="field-label">ยืนยันด้วยวิธีใด</div>
          <select
            value={selected?.code ?? ""}
            onChange={(e) => setChallenge(e.target.value)}
            disabled={disabled}
          >
            {challenges.map((option) => (
              <option key={option.code} value={option.code}>
                {option.label_th}
              </option>
            ))}
          </select>
        </div>
        {selected?.requires_note && (
          <input
            placeholder="ระบุว่ายืนยันตัวตนด้วยวิธีใด"
            value={challengeNote}
            onChange={(e) => setChallengeNote(e.target.value)}
            disabled={disabled}
          />
        )}
        <button
          className="primary"
          disabled={disabled || needNote || !can("confirmed")}
          title={can("confirmed") ? undefined : lockedHint}
          onClick={() =>
            onAttest({
              outcome: "confirmed",
              challenge: selected?.code,
              challenge_note: challengeNote || null,
              amend: reopened,
            })
          }
        >
          ยืนยันว่าใช่บุคคลนี้
        </button>

        <div style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
          <div className="field-label">ผู้ดำเนินการแทน (ต้องกรอกทั้งสองช่อง)</div>
          {/* Two different sentences, because the button means two different things
              (`D71`). Normally it IS the authority check: pressing it says both "this
              person may act for the policyholder" and "I satisfied myself of that", which
              is why the label carries the obligation (`D65`). But when the call arrived on
              an app token the account holder already authenticated, so the agent is not
              vouching for anything — they are recording that somebody else is operating
              the session, which is the only fact still unknown. */}
          <div className="faint" style={{ marginBottom: 6 }}>
            {identity.system_verified
              ? "ยืนยันตัวตนผ่านแอปแล้ว — บันทึกเพิ่มได้ว่ามีผู้อื่นดำเนินการแทนเจ้าของบัญชี"
              : "กดเมื่อตรวจสอบแล้วว่ามีสิทธิ์ดำเนินการแทน — ระบบจะบันทึกว่าเป็น “ผู้ดำเนินการแทน” ไม่ใช่เจ้าของกรมธรรม์"}
          </div>
          <div className="stack">
            <input
              placeholder="ชื่อ-นามสกุลผู้ติดต่อ"
              value={callerName}
              onChange={(e) => setCallerName(e.target.value)}
              disabled={disabled}
            />
            <input
              placeholder="ความสัมพันธ์ เช่น ลูกสาว"
              value={relationship}
              onChange={(e) => setRelationship(e.target.value)}
              disabled={disabled}
            />
            <button
              disabled={disabled || !thirdPartyReady || !can("third_party")}
              title={can("third_party") ? undefined : lockedHint}
              onClick={() =>
                onAttest({
                  outcome: "third_party",
                  caller_name: callerName,
                  relationship,
                  amend: reopened,
                })
              }
            >
              {identity.system_verified ? "บันทึกว่ามีผู้ดำเนินการแทน" : "ยืนยันว่ามีสิทธิ์ดำเนินการแทน"}
            </button>
          </div>
        </div>

        <button
          className="danger"
          disabled={disabled || !can("not_this_person")}
          title={can("not_this_person") ? undefined : lockedHint}
          onClick={() => onAttest({ outcome: "not_this_person", amend: reopened })}
        >
          ไม่ใช่บุคคลนี้
        </button>
      </div>
    </div>
  );
}

// --- keypad capture (D44, D58) --------------------------------------------

export function CapturePanel({
  capture,
  callId,
  onStart,
  onKey,
  onBackspace,
  onStop,
  onDiscard,
  onLookup,
  busy,
}: {
  capture: Capture | null;
  callId: string | null;
  onStart: () => void;
  onKey: (digit: string) => void;
  onBackspace: () => void;
  onStop: () => void;
  onDiscard: () => void;
  onLookup: (kind: string) => void;
  busy: boolean;
}) {
  const open = capture?.state === "open";
  return (
    <div className="panel">
      <h2>รับตัวเลขจากปุ่มกด</h2>
      <p className="faint">
        กดเริ่ม แล้วให้ลูกค้ากดตัวเลขอะไรก็ได้ที่มีอยู่ตรงหน้า — เลขกรมธรรม์ เลขเคลม
        เลขบัตรประชาชน หรืออย่างอื่น ระบบไม่เดาว่าคืออะไร
      </p>

      {/* The agent sees the real digits. `D44`'s inverted default is "masked in
          transcripts and logs" — the agent is the person the capture was made FOR, and
          they have to read these back. Masking them here deletes the feature and protects
          nothing (`D58`). */}
      <div className="digits mono">{capture?.digits || (open ? "…" : "")}</div>

      <div className="row" style={{ marginTop: 8 }}>
        {!open && (
          <button className="primary" onClick={onStart} disabled={busy || !callId}>
            เริ่มรับตัวเลข
          </button>
        )}
        {open && (
          <>
            <button onClick={onStop} disabled={busy}>
              หยุด
            </button>
            <button onClick={onBackspace} disabled={busy || !capture?.length}>
              ลบทีละตัว
            </button>
            <button className="danger" onClick={onDiscard} disabled={busy}>
              ทิ้งตัวเลข
            </button>
          </>
        )}
      </div>

      {open && (
        // DEMO: real DTMF arrives from Asterisk at P5. This pad stands in for the
        // customer's handset so the panel can be driven on stage today.
        <div className="keypad">
          {["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"].map((digit) => (
            <button key={digit} onClick={() => onKey(digit)} disabled={busy}>
              {digit}
            </button>
          ))}
        </div>
      )}

      {capture && capture.state !== "open" && capture.length > 0 && (
        <div className="stack" style={{ marginTop: 10 }}>
          {/* The titles say what the agent may ASK for, because the matcher now accepts
              all of it (`D66`): the whole number, the last N, the first N, or — for a
              date — day+month or the year alone, in either era (`D67`). The old labels
              implied the caller had to key the entire thing. */}
          <div className="faint">
            เทียบว่าเลขนี้ตรงกับอะไร — เป็นเพียงหลักฐาน ไม่เปลี่ยนระดับการยืนยันตัวตน
            <br />
            ขอเป็นเลขเต็ม ตัวท้าย หรือตัวแรกก็ได้ ระบบจะบอกว่าตรงแบบไหน
          </div>
          <div className="row">
            <button
              onClick={() => onLookup("policy_number")}
              disabled={busy}
              title="รับได้ทั้งเลขเต็ม, N ตัวท้าย หรือ N ตัวแรก (อย่างน้อย 3 หลัก)"
            >
              เทียบเลขกรมธรรม์
            </button>
            <button
              onClick={() => onLookup("claim_number")}
              disabled={busy}
              title="รับได้ทั้งเลขเต็ม, N ตัวท้าย หรือ N ตัวแรก (อย่างน้อย 3 หลัก)"
            >
              เทียบเลขเคลม
            </button>
            <button
              onClick={() => onLookup("date_of_birth")}
              disabled={busy}
              title="รับได้ทั้ง ววดดปปปป, ปปปปดดวว, ววดด หรือปีเกิดอย่างเดียว — ค.ศ. หรือ พ.ศ. ก็ได้"
            >
              เทียบวันเกิด / ปีเกิด
            </button>
          </div>
          {capture.lookups.map((lookup, index) => (
            <div key={index} className={`badge ${lookup.matched ? "ok" : "bad"}`}>
              {LOOKUP_LABEL[lookup.kind] ?? lookup.kind}: {lookup.matched ? "ตรงกัน" : "ไม่ตรง"}
              {lookup.matched_value ? ` · ${lookup.matched_value}` : ""}
              {lookup.detail ? ` · ${lookup.detail}` : ""}
            </div>
          ))}
          {capture.lookups.length > 0 && (
            <div className="hint">
              ต้องกดยืนยันตัวตนเองที่แผงด้านบน — ผลการเทียบไม่ได้ยืนยันว่าใครถือโทรศัพท์อยู่
            </div>
          )}
          <button className="ghost danger" onClick={onDiscard} disabled={busy}>
            ทิ้งตัวเลขนี้
          </button>
        </div>
      )}
    </div>
  );
}

// --- the brief ------------------------------------------------------------

/** *"What else do we know about this person"* (`D134`).
 *
 * Everything here was already assembled on every call and thrown away: `ContextAssembler`
 * reads holdings, life events and the conversation history into the frozen snapshot, and
 * the brief exposed two counts and one sentence. This is the brief's own promise arriving
 * — the broker does not have to ask for what the bank already holds.
 *
 * ⚠️ It SHOWS and it does not RECOMMEND (`D116`). Consent is required before personalised
 * recommendation; displaying a record an affiliate lawfully shared is internal processing
 * (`D74`). Nothing in this panel says what to sell, and the server refuses a summary that
 * does.
 */
function KnownAboutPanel({ context }: { context: BriefContext }) {
  const [open, setOpen] = useState(false);
  const groups: [string, string][] = [
    ["life_event", "สัญญาณจากชีวิตลูกค้า"],
    ["holding", "ผลิตภัณฑ์ที่ถืออยู่กับธนาคาร"],
    ["interaction", "ติดต่อล่าสุด"],
  ];
  return (
    <div className="known-about">
      <div className="faint" style={{ marginTop: 12 }}>
        ข้อมูลอื่นๆ ที่มีเกี่ยวกับลูกค้า · {context.facts.length} รายการ
      </div>

      {context.summary_th && <div className="known-summary">{context.summary_th}</div>}
      {context.summary_unavailable && (
        <div className="faint" style={{ marginTop: 4 }}>
          ยังไม่ได้ตั้งค่าโมเดล — แสดงเฉพาะข้อมูลดิบด้านล่าง
        </div>
      )}

      {/* The raw half, folded away. `D18`: a broker who cannot say WHERE a fact came from
          cannot use it in a conversation — so every row carries its source, and the
          summary above is checkable against the rows rather than taken on trust. */}
      <button
        className="ghost"
        style={{ marginTop: 8 }}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? "▾ ซ่อนข้อมูลดิบ" : "▸ ดูข้อมูลดิบทั้งหมด"}
      </button>

      {open && (
        <div className="known-raw">
          {groups.map(([kind, label]) => {
            const rows = context.facts.filter((f) => f.kind === kind);
            if (!rows.length) return null;
            return (
              <div key={kind} style={{ marginTop: 8 }}>
                <div className="faint">{label}</div>
                {rows.map((f, i) => (
                  <div className="known-row" key={`${kind}-${i}`}>
                    <div>
                      <strong>{f.label_th}</strong>
                      {f.detail_th && <span className="faint"> · {f.detail_th}</span>}
                      {/* ⚠️ An INFERRED fact is labelled as one. `income_pattern` at 0.6
                          is a guess and `loan_origination` at 0.95 is nearly a record,
                          and a broker who cannot tell them apart will say the wrong one
                          out loud. */}
                      {f.confidence != null && (
                        <span className="badge" style={{ marginLeft: 6 }}>
                          {f.confidence >= 0.8 ? "ค่อนข้างแน่ใจ" : "เป็นการอนุมาน"}{" "}
                          {Math.round(f.confidence * 100)}%
                        </span>
                      )}
                    </div>
                    <div className="faint">
                      {f.at_th ? `${f.at_th} · ` : ""}
                      {f.source}
                    </div>
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function BriefPanel({ brief }: { brief: Brief }) {
  if (!brief) {
    return (
      <div className="panel">
        <h2>สรุปก่อนคุย</h2>
        <p className="faint">ยังไม่มีสายที่กำลังสนทนา</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h2>
        สรุปก่อนคุย · v{brief.version}
        {brief.build_ms !== null && (
          <span className="faint"> · สร้างใน {brief.build_ms.toFixed(1)} ms</span>
        )}
      </h2>

      <div className="brief-line">
        <span className="k">เรื่อง</span>
        <span>
          {brief.intent?.label_th ?? "—"}{" "}
          <span className="faint mono">{brief.intent?.source}</span>
        </span>
      </div>
      <div className="brief-line">
        <span className="k">ลูกค้า</span>
        <span>
          {brief.customer ? (
            <>
              {brief.customer.display_name_th}
              {brief.disclosure_locked && (
                <span className="faint"> · ยังไม่ยืนยันตัวตน อย่าเอ่ยชื่อก่อนถาม</span>
              )}
              {brief.customer.is_vulnerable && (
                <span className="badge warn" style={{ marginLeft: 8 }}>
                  ต้องดูแลเป็นพิเศษ
                </span>
              )}
            </>
          ) : (
            <em className="locked">ยังไม่ทราบว่าเป็นใคร</em>
          )}
        </span>
      </div>

      {brief.handoff_to_insurer && (
        /* Said BEFORE the agent starts talking, because "I will check and call you back"
           and "I am passing you to the insurer now" are different promises and only one
           of them is the broker's to make (`D117`). */
        <div className="handoff-note">
          เรื่องนี้ต้องส่งต่อบริษัทประกัน — เราช่วยรวบรวมข้อมูลและประสานงานให้
        </div>
      )}

      <div className="brief-line">
        <span className="k">กรมธรรม์</span>
        <span>
          {brief.relevant_policy ? (
            <>
              <span className="mono">{brief.relevant_policy.policy_no}</span>
              {brief.relevant_policy.product_th ? ` · ${brief.relevant_policy.product_th}` : ""}
              {/* The carrier, on its own line and not greyed out (`D117`). A broker
                  holds one customer across several insurers, so "which company" is the
                  first thing the agent needs — for a claim it decides who they are about
                  to be handed to. */}
              {brief.relevant_policy.insurer && (
                <div className="insurer">บริษัทผู้รับประกัน: {brief.relevant_policy.insurer}</div>
              )}
              <div className="faint">
                {brief.relevant_policy.status}
                {brief.relevant_policy.sum_insured
                  ? ` · ทุนประกัน ${brief.relevant_policy.sum_insured.toLocaleString()} บาท`
                  : ""}
                {brief.other_policy_count > 0 ? ` · อีก ${brief.other_policy_count} ฉบับ` : ""}
              </div>
            </>
          ) : (
            <em className="locked">
              {brief.disclosure_locked
                ? "ปกปิดจนกว่าจะยืนยันตัวตน"
                : "ไม่พบกรมธรรม์ที่เกี่ยวข้อง"}
            </em>
          )}
        </span>
      </div>

      {brief.relevant_policy && brief.relevant_policy.coverages.length > 0 && (
        <div className="brief-line">
          <span className="k">ความคุ้มครอง</span>
          <span>
            {brief.relevant_policy.coverages.map((coverage, index) => (
              <div key={index}>
                {coverage.label_th}
                {coverage.limit_text ? ` · ${coverage.limit_text}` : ""}
              </div>
            ))}
          </span>
        </div>
      )}

      {brief.summary_th && (
        <div className="brief-line">
          <span className="k">สรุป</span>
          <span>
            {brief.summary_th}
            {/* `D131`: say which of the two passes wrote this. Without it the summary
                silently rewords itself a few seconds into the call and the agent has no
                way to tell an upgrade from a glitch (`D68`). */}
            {brief.summary_is_preview && (
              <span className="badge" style={{ marginLeft: 6, verticalAlign: "middle" }}>
                สรุประหว่างรอรับสาย
              </span>
            )}
          </span>
        </div>
      )}
      {brief.last_contact_th && (
        <div className="brief-line">
          <span className="k">ติดต่อล่าสุด</span>
          <span>{brief.last_contact_th}</span>
        </div>
      )}

      {brief.context && brief.context.facts.length > 0 && (
        <KnownAboutPanel context={brief.context} />
      )}

      {brief.actions_th.length > 0 && (
        <>
          <div className="faint" style={{ marginTop: 10 }}>
            สิ่งที่ควรทำ · จาก playbook ของ intent นี้ ไม่ใช่ AI เขียน
          </div>
          <ol className="actions">
            {brief.actions_th.map((action, index) => (
              <li key={index}>{action}</li>
            ))}
          </ol>
        </>
      )}

      {brief.suggested_opening_th && (
        <div className="opening" style={{ marginTop: 10 }}>
          <div className="faint">ประโยคเปิด</div>
          {brief.suggested_opening_th}
        </div>
      )}

      {brief.provenance.length > 0 && (
        <details style={{ marginTop: 10 }}>
          <summary className="faint">ข้อมูลนี้มาจากไหน ({brief.provenance.length} field)</summary>
          {brief.provenance.map((entry) => (
            <div key={entry.field} className="row faint" style={{ fontSize: 12 }}>
              <span className="mono">{entry.field}</span>
              <div className="spacer" />
              <span>{entry.source}</span>
              {entry.stale && <span className="badge warn">stale</span>}
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

// --- queues ---------------------------------------------------------------

/**
 * Queue depth, split into what this agent can act on and what they cannot (`D70`).
 *
 * Each row is a QUEUE and its depth, not a caller — worth saying, because the strip reads
 * like a list of people until you notice the numbers. All nine queues used to be listed
 * identically, so a health agent watched motor and life fill up with no way to tell which
 * of those numbers were theirs. `mine` comes from the server, which already owns the
 * skill-to-queue mapping the matcher uses.
 */
export function QueueStrip({ queues, skewMs = 0 }: { queues: Queue[]; skewMs?: number }) {
  const [showAll, setShowAll] = useState(false);
  // The strip's wait is a clock like every other one on this screen (`B27`), so it needs
  // the same once-a-second re-render. Only while somebody is actually waiting.
  const now = useSecondTicker(queues.some((q) => q.waiting > 0));
  const mine = queues.filter((q) => q.mine);
  const shown = showAll ? queues : mine;
  const othersWaiting = queues.filter((q) => !q.mine).reduce((n, q) => n + q.waiting, 0);

  return (
    <div className="panel">
      <div className="queue-head">
        <h2 style={{ margin: 0 }}>คิว</h2>
        <div className="spacer" />
        <button
          className={showAll ? "ghost" : "ghost on"}
          onClick={() => setShowAll(false)}
          title="เฉพาะคิวที่คุณมีทักษะรับได้"
        >
          ของฉัน <span className="count">({mine.length})</span>
        </button>
        <button
          className={showAll ? "ghost on" : "ghost"}
          onClick={() => setShowAll(true)}
          title="ทุกคิวในระบบ รวมคิวที่คุณรับไม่ได้"
        >
          ทั้งหมด <span className="count">({queues.length})</span>
        </button>
      </div>

      {shown.length === 0 && <p className="faint">ไม่มีคิวที่ตรงกับทักษะของคุณ</p>}

      {shown.map((queue) => (
        <div className="queue" key={queue.queue_id}>
          <span className="dot" style={{ color: queue.is_open ? "var(--ok)" : "var(--bad)" }} />
          <span style={{ opacity: queue.mine ? 1 : 0.55 }}>
            {queue.label_th}
            {showAll && !queue.mine && <span className="faint"> · รับไม่ได้</span>}
          </span>
          <div className="spacer" />
          {queue.is_open ? (
            // The time is the LONGEST wait in this queue, not an average and not
            // necessarily the next caller out: matching is global and urgency-weighted
            // (`D22`), so a newer caller at an accident scene can legitimately go first.
            <span className="mono muted" title="เวลารอของคนที่รอนานที่สุดในคิวนี้">
              {queue.waiting} รอ · รอนานสุด{" "}
              {mmss(elapsedSince(queue.longest_wait_since, now, skewMs) ?? queue.longest_wait_s)}
            </span>
          ) : (
            <span className="faint">
              {queue.closed_reason === "holiday" ? "วันหยุด" : "นอกเวลาทำการ"}
            </span>
          )}
        </div>
      ))}

      {!showAll && othersWaiting > 0 && (
        <div className="faint" style={{ marginTop: 6 }}>
          อีก {othersWaiting} สายรออยู่ในคิวที่คุณรับไม่ได้
        </div>
      )}
    </div>
  );
}

// --- wrap-up (D45, D59) ---------------------------------------------------

/** Disposition codes and their Thai labels, in one place: the form offers them and the
 *  saved summary reads them back, and two copies would eventually disagree. */
const DISPOSITIONS: Record<string, string> = {
  advice_given: "ให้คำแนะนำแล้ว",
  quote_sent: "ส่งใบเสนอราคา",
  renewed: "ต่ออายุแล้ว",
  // A broker does not adjudicate claims (`D117`), so "เปิดเคลม" was describing somebody
  // else's work. What we actually did is take the notification and hand it over.
  handed_to_insurer: "ส่งต่อบริษัทประกัน",
  claim_opened: "เปิดเคลม",
  document_sent: "ส่งเอกสาร",
  escalated: "ส่งต่อ",
  callback_scheduled: "นัดโทรกลับ",
};

/** The backlog of calls nobody filed anything for (`D87`).
 *
 *  `D45` says the person decides when after-call work ends, so they may leave the form
 *  half-written — for a bathroom break, an escalation, or a mis-click on a status button
 *  sitting right beside it. None of those should cost the customer a record, so the call
 *  waits here instead of vanishing.
 *
 *  Deliberately NOT a modal and NOT a block. It is a standing offer: clear it when you
 *  have a moment.
 */
export function BacklogPanel({
  pending,
  busy,
  onPick,
}: {
  pending: PendingWrapup[];
  busy: boolean;
  onPick: (callId: string) => void;
}) {
  if (pending.length === 0) return null;
  return (
    <div className="panel backlog">
      <h2>
        สรุปที่ยังไม่ได้บันทึก <span className="badge warn">{pending.length}</span>
      </h2>
      <p className="faint">
        สายที่จบแล้วแต่ยังไม่ได้บันทึกสรุป — เลือกเพื่อบันทึกย้อนหลังได้
      </p>
      <div className="stack">
        {pending.map((row) => (
          <button
            key={row.call_session_id}
            className="backlog-row"
            disabled={busy}
            onClick={() => onPick(row.call_session_id)}
          >
            <span className="what">
              {row.intent_label_th ?? row.intent_code ?? "ไม่ระบุเรื่อง"}
              {row.customer_name_th && <em> · {row.customer_name_th}</em>}
            </span>
            <span className="when">
              {row.ended_at ? new Date(row.ended_at).toLocaleTimeString("th-TH", {
                hour: "2-digit",
                minute: "2-digit",
              }) : "—"}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function WrapupPanel({
  presence,
  saved,
  onSave,
  onSaveAndDeclare,
  busy,
  lateFor = null,
  onCancel,
  handoff = null,
}: {
  presence: Presence;
  saved: boolean;
  onSave: (payload: Record<string, unknown>) => void;
  onSaveAndDeclare: (payload: Record<string, unknown>, intent: string) => void;
  busy: boolean;
  /** The handoff this call ended in, if it did (`D124`). A **suggestion**: it fills the
   *  disposition and the note once, and the agent edits or replaces it. `D45` says
   *  nothing is saved on their behalf, and they are the one who made the promise to the
   *  customer — so `was_edited` still distinguishes a confirmed disposition from an
   *  accepted default. */
  handoff?: Handoff | null;
  /** Set when filing a call from the backlog rather than wrapping up the live one
   *  (`D87`). Changes the framing and drops the presence buttons — the agent may be on
   *  another call entirely while they do this. */
  lateFor?: PendingWrapup | null;
  onCancel?: () => void;
}) {
  const [disposition, setDisposition] = useState("advice_given");
  const [notes, setNotes] = useState("");
  const [followUp, setFollowUp] = useState(false);

  // Applied ONCE per handoff, keyed on its timestamp. Two hazards, and they pull in
  // opposite directions: initialising `useState` from the prop misses the case where the
  // panel is already mounted when the handoff lands, and re-applying on every render
  // would wipe whatever the agent has typed since — the optimistic-UI hazard `D120`'s
  // customer page already taught, in a form somebody is mid-sentence in.
  const appliedHandoff = useRef<string | null>(null);
  useEffect(() => {
    if (!handoff || appliedHandoff.current === handoff.at) return;
    appliedHandoff.current = handoff.at;
    setDisposition("handed_to_insurer");
    setNotes(handoff.disposition_th);
  }, [handoff]);
  const payload = () => ({
    disposition,
    notes,
    follow_up_required: followUp,
    was_edited: notes.trim().length > 0,
  });

  // The one-click combination only makes sense if "ready" is where the agent is actually
  // going. Two cases where it is not, and they look identical without `intent_reason`:
  // after a LAST_CALL the standing instruction has been spent (`D59`), and while DRAINING
  // they explicitly asked for no new callers. Offering "Save & Ready" in either case
  // makes the fastest button the one that undoes what they just told us.
  const late = lateFor !== null;
  const lastCallSpent = presence.intent_reason === "last_call_fulfilled";
  const draining = presence.agent_intent === "draining";
  const canOfferReady =
    !late && presence.declarable.includes("ready") && !draining && !lastCallSpent;

  // Once saved, the form collapses to a read-only confirmation. Leaving the editable
  // fields on screen made a finished record look like outstanding work — the agent could
  // not tell at a glance whether they still owed anything (`B11`). ACW itself keeps
  // running either way, which is `D45` and is what the line below says.
  if (saved) {
    return (
      <div className="panel">
        <h2>สรุปหลังจบสาย</h2>
        <div className="saved-note">
          <b>บันทึกเรียบร้อยแล้ว</b>
          <div className="faint">
            บันทึกของสายนี้ปิดแล้ว — งานหลังสายยังไม่จบจนกว่าคุณจะเลือกสถานะถัดไป
          </div>
        </div>
        <dl className="saved-summary">
          <dt>ผลการติดต่อ</dt>
          <dd>{DISPOSITIONS[disposition] ?? disposition}</dd>
          {notes.trim() && (
            <>
              <dt>บันทึกการสนทนา</dt>
              <dd>{notes}</dd>
            </>
          )}
          {followUp && (
            <>
              <dt>ติดตามต่อ</dt>
              <dd>ต้องติดตามต่อ</dd>
            </>
          )}
        </dl>
        <div className="row">
          {canOfferReady && (
            <button
              className="primary"
              onClick={() => onSaveAndDeclare(payload(), "ready")}
              disabled={busy}
            >
              พร้อมรับสาย
            </button>
          )}
          {draining && (
            <button
              className="primary"
              onClick={() => onSaveAndDeclare(payload(), "draining")}
              disabled={busy}
            >
              กลับสู่โหมดไม่รับสายใหม่
            </button>
          )}
        </div>
        <div className="hint">เลือกสถานะอื่นได้ที่แผงสถานะทางซ้าย</div>
      </div>
    );
  }

  return (
    <div className={`panel ${late ? "late" : ""}`}>
      <h2>{late ? "บันทึกสรุปย้อนหลัง" : "สรุปหลังจบสาย"}</h2>
      {late ? (
        <p className="faint">
          {lateFor.intent_label_th ?? lateFor.intent_code ?? "สายที่ยังไม่ได้บันทึก"}
          {lateFor.customer_name_th ? ` · ${lateFor.customer_name_th}` : ""}
          {lateFor.ended_at
            ? ` · จบสายเมื่อ ${new Date(lateFor.ended_at).toLocaleTimeString("th-TH", {
                hour: "2-digit",
                minute: "2-digit",
              })}`
            : ""}
        </p>
      ) : (
        <p className="faint">
          การบันทึกจะปิด “บันทึกของสายนี้” เท่านั้น — งานหลังสายจะจบเมื่อคุณเลือกสถานะถัดไป
        </p>
      )}

      <div className="stack" style={{ marginTop: 8 }}>
        <select value={disposition} onChange={(e) => setDisposition(e.target.value)}>
          {Object.entries(DISPOSITIONS).map(([code, label]) => (
            <option key={code} value={code}>
              {label}
            </option>
          ))}
        </select>
        <textarea
          rows={4}
          placeholder="บันทึกการสนทนา"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
        <label className="row" style={{ fontSize: 13 }}>
          <input
            type="checkbox"
            style={{ width: "auto" }}
            checked={followUp}
            onChange={(e) => setFollowUp(e.target.checked)}
          />
          ต้องติดตามต่อ
        </label>
        <div className="row">
          <button onClick={() => onSave(payload())} disabled={busy}>
            บันทึก
          </button>
          {late && onCancel && (
            <button className="ghost" onClick={onCancel} disabled={busy}>
              ยกเลิก
            </button>
          )}
          {canOfferReady && (
            <button
              className="primary"
              onClick={() => onSaveAndDeclare(payload(), "ready")}
              disabled={busy}
            >
              บันทึกแล้วพร้อมรับสาย
            </button>
          )}
          {draining && (
            <button
              className="primary"
              onClick={() => onSaveAndDeclare(payload(), "draining")}
              disabled={busy}
            >
              บันทึกแล้วกลับสู่โหมดไม่รับสายใหม่
            </button>
          )}
        </div>
        {lastCallSpent && (
          <div className="hint">
            คุณตั้งไว้ว่าสายนั้นเป็น “สายสุดท้าย” และสายจบแล้ว — บันทึกได้เลย
            แล้วเลือกสถานะถัดไปเองที่แผงสถานะ
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * The live transcript of the call (`D106`).
 *
 * What the pitch is actually about: by the time the agent presses Accept, the caller has
 * already explained the problem and it is on screen, in their own words, with the moment
 * in the recording each sentence was said.
 *
 * Three things it deliberately does not do:
 *
 * * **It does not gate on assurance.** This is the caller's own speech on the call being
 *   taken, not a lookup, and an anonymous caller at L0 is precisely the case with no other
 *   source of context. `D74` gates what may be said and done; `D53` gates the record. This
 *   is neither.
 * * **It does not hide a low-confidence turn.** A dropped sentence leaves a silent gap
 *   that reads as the caller having said nothing, which is a worse lie than a hedged line
 *   (`D16`). Low confidence is shown as a mark on the turn instead.
 * * **It does not accumulate.** `snapshot.transcript` is replaced wholesale on every push,
 *   so there is no local list here that could drift from the server's.
 */
export function TranscriptPanel({
  turns,
  hasCall,
  degraded,
}: {
  turns: TranscriptTurn[];
  hasCall: boolean;
  degraded?: string;
}) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const [pinned, setPinned] = useState(true);

  useEffect(() => {
    // Follow the newest line, but only while the agent is already at the bottom. Yanking
    // the view back down while somebody is re-reading what was said two minutes ago is
    // the single most irritating thing a live log can do.
    if (pinned) endRef.current?.scrollIntoView({ block: "nearest" });
  }, [turns.length, pinned]);

  return (
    <div className="panel">
      <h2>
        บทสนทนาก่อนรับสาย
        {turns.length > 0 && <span className="faint"> · {turns.length} ประโยค</span>}
      </h2>
      {turns.length === 0 ? (
        <p className="faint">{emptyTranscriptReason(hasCall, degraded)}</p>
      ) : (
        <div
          className="transcript"
          onScroll={(event) => {
            const el = event.currentTarget;
            setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 24);
          }}
        >
          {turns.map((turn) => (
            <div className="turn" key={turn.turn_id}>
              <span className="at mono">{mmss(Math.floor(turn.t_start_ms / 1000))}</span>
              <span className="said">
                {turn.text}
                {turn.asr_confidence !== null && turn.asr_confidence < 0.6 && (
                  <span className="faint" title="ระบบถอดความไม่มั่นใจในประโยคนี้">
                    {" "}
                    · ไม่ชัด
                  </span>
                )}
              </span>
            </div>
          ))}
          <div ref={endRef} />
        </div>
      )}
      {turns.length > 0 && (
        <div className="hint">
          ถอดความอัตโนมัติ — ใช้เป็นบริบท ไม่ใช่คำยืนยัน ตัวเลขให้ยืนยันกับลูกค้าอีกครั้ง
        </div>
      )}
    </div>
  );
}

/**
 * Why the panel is empty, which is never nothing (`D88`).
 *
 * "They declined", "we never asked" and "the transcriber was down" are three different
 * facts and an agent looking at a thin brief is owed which one it is. The server already
 * distinguishes them on the brief's `degraded`; this just says it in Thai.
 */
function emptyTranscriptReason(hasCall: boolean, degraded?: string): string {
  if (!hasCall) return "ยังไม่มีสายที่กำลังสนทนา";
  if (degraded === "intake_declined") return "ลูกค้าไม่ประสงค์ให้บันทึกเสียง";
  if (degraded === "no_consent") return "ไม่ได้ขอความยินยอมบันทึกเสียงในสายนี้";
  if (degraded === "stt_unavailable") return "ระบบถอดความไม่พร้อมใช้งานในสายนี้";
  return "ลูกค้าไม่ได้พูดอะไรระหว่างรอสาย";
}
