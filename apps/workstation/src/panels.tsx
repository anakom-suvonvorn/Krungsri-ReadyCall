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

import { useEffect, useState } from "react";
import type { Brief, Capture, Identity, Offer, Presence, Queue } from "./api";

/** Named challenges plus the escape hatch (`D57`). `other` is not a fallback for a
 *  missing case — it is the case: recognised the voice from last week, read a claim
 *  reference off an SMS, transferred in from a branch that already checked ID. */
const CHALLENGES = [
  { value: "date_of_birth", label: "วันเกิด" },
  { value: "citizen_id_last4", label: "เลขบัตรประชาชน 4 ตัวท้าย" },
  { value: "policy_number", label: "เลขกรมธรรม์" },
  { value: "recent_claim_amount", label: "ยอดเคลมล่าสุด" },
  { value: "other", label: "อื่น ๆ (ระบุเอง)" },
];

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
};

const OUTCOME_LABEL: Record<string, string> = {
  confirmed: "ยืนยันแล้วว่าเป็นเจ้าของกรมธรรม์",
  third_party: "ผู้ดำเนินการแทน",
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

export function elapsedSince(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null;
  const started = Date.parse(iso);
  return Number.isNaN(started) ? null : (now - started) / 1000;
}

// --- the offer card -------------------------------------------------------

export function OfferCard({
  offer,
  onAccept,
  onDecline,
  busy,
}: {
  offer: Offer | null;
  onAccept: () => void;
  onDecline: (reason: string) => void;
  busy: boolean;
}) {
  const now = useSecondTicker(offer !== null);
  if (!offer) return <div className="scrim hidden" />;

  const left = Math.max(0, offer.timeout_s - (elapsedSince(offer.offered_at, now) ?? 0));
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
          <span className="badge">รอมาแล้ว {mmss(offer.waited_s)}</span>
        </div>

        {offer.rationale_th && (
          <div className="rationale">
            <div className="faint">ทำไมถึงเป็นคุณ</div>
            <div>{offer.rationale_th}</div>
          </div>
        )}

        <div className="row" style={{ marginTop: 14 }}>
          <button className="primary" onClick={onAccept} disabled={busy}>
            รับสาย (Accept)
          </button>
          <button className="ghost" onClick={() => onDecline("busy")} disabled={busy}>
            ไม่รับ (Decline)
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
  callId,
  onAttest,
  busy,
}: {
  identity: Identity | null;
  callId: string | null;
  onAttest: (payload: Record<string, unknown>) => void;
  busy: boolean;
}) {
  const [challenge, setChallenge] = useState(CHALLENGES[0].value);
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
  const needNote = challenge === "other" && !challengeNote.trim();
  const thirdPartyReady = callerName.trim().length > 0 && relationship.trim().length > 0;
  // After *ไม่ใช่บุคคลนี้* there is no proposed customer left to confirm or to act for, so
  // the server refuses both with a 400 (`attestation.py`). Customer search is not built
  // yet (`Q18`), so this is a real dead end for the rest of the call — and it must LOOK
  // like one. A live-looking button that always errors reads as a broken screen.
  const noCustomer = identity.customer_id === null;
  const noCustomerHint = noCustomer
    ? "ไม่มีลูกค้าที่ระบบเสนอไว้แล้ว — ต้องค้นหาลูกค้าก่อน (ยังไม่มีในระบบ)"
    : undefined;

  return (
    <div className="panel">
      <h2>ตัวตนผู้ติดต่อ</h2>
      <AssuranceBadge assurance={identity.assurance} />
      <div className="faint" style={{ marginTop: 6 }}>
        {identity.may_disclose_policy_details
          ? "เปิดเผยรายละเอียดกรมธรรม์ได้"
          : "ยังเปิดเผยรายละเอียดกรมธรรม์ไม่ได้"}
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

      {identity.authority_check_required && (
        <div className="rationale" style={{ borderLeftColor: "var(--warn)" }}>
          ผู้ติดต่อดำเนินการแทนเจ้าของกรมธรรม์ — ตรวจสอบสิทธิ์ในการดำเนินการก่อน
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
            value={challenge}
            onChange={(e) => setChallenge(e.target.value)}
            disabled={disabled}
          >
            {CHALLENGES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        {challenge === "other" && (
          <input
            placeholder="ระบุว่ายืนยันตัวตนด้วยวิธีใด"
            value={challengeNote}
            onChange={(e) => setChallengeNote(e.target.value)}
            disabled={disabled}
          />
        )}
        <button
          className="primary"
          disabled={disabled || needNote || noCustomer}
          title={noCustomerHint}
          onClick={() =>
            onAttest({
              outcome: "confirmed",
              challenge,
              challenge_note: challengeNote || null,
              amend: reopened,
            })
          }
        >
          ยืนยันว่าใช่บุคคลนี้
        </button>

        <div style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
          <div className="field-label">ผู้ดำเนินการแทน (ต้องกรอกทั้งสองช่อง)</div>
          {/* Says out loud what `D42` decided, because the screen looked like a bug
              otherwise: third party is NOT a promotion. The case context attaches so the
              agent can see which policy this is about; disclosure stays locked, because a
              relative holding the documents is not the policyholder. */}
          <div className="faint" style={{ marginBottom: 6 }}>
            บันทึกว่าเป็นผู้ดำเนินการแทน — ยังไม่เปิดเผยรายละเอียดกรมธรรม์ และจะมีขั้นตอนตรวจสอบสิทธิ์
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
              disabled={disabled || !thirdPartyReady || noCustomer}
              title={noCustomerHint}
              onClick={() =>
                onAttest({
                  outcome: "third_party",
                  caller_name: callerName,
                  relationship,
                  amend: reopened,
                })
              }
            >
              ยืนยันว่าเป็นผู้ดำเนินการแทน
            </button>
          </div>
        </div>

        <button
          className="danger"
          disabled={disabled}
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
          <div className="faint">
            เทียบว่าเลขนี้ตรงกับอะไร — เป็นเพียงหลักฐาน ไม่เปลี่ยนระดับการยืนยันตัวตน
          </div>
          <div className="row">
            <button onClick={() => onLookup("policy_number")} disabled={busy}>
              เทียบเลขกรมธรรม์
            </button>
            <button onClick={() => onLookup("claim_number")} disabled={busy}>
              เทียบเลขเคลม
            </button>
            <button onClick={() => onLookup("date_of_birth")} disabled={busy}>
              เทียบวันเกิด
            </button>
          </div>
          {capture.lookups.map((lookup, index) => (
            <div key={index} className={`badge ${lookup.matched ? "ok" : "bad"}`}>
              {lookup.kind}: {lookup.matched ? "ตรงกัน" : "ไม่ตรง"}
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

      <div className="brief-line">
        <span className="k">กรมธรรม์</span>
        <span>
          {brief.relevant_policy ? (
            <>
              <span className="mono">{brief.relevant_policy.policy_no}</span>
              {brief.relevant_policy.product_th ? ` · ${brief.relevant_policy.product_th}` : ""}
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
          <span>{brief.summary_th}</span>
        </div>
      )}
      {brief.last_contact_th && (
        <div className="brief-line">
          <span className="k">ติดต่อล่าสุด</span>
          <span>{brief.last_contact_th}</span>
        </div>
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

export function QueueStrip({ queues }: { queues: Queue[] }) {
  return (
    <div className="panel">
      <h2>คิว</h2>
      {queues.map((queue) => (
        <div className="queue" key={queue.queue_id}>
          <span className="dot" style={{ color: queue.is_open ? "var(--ok)" : "var(--bad)" }} />
          <span>{queue.label_th}</span>
          <div className="spacer" />
          {queue.is_open ? (
            <span className="mono muted">
              {queue.waiting} รอ · {mmss(queue.longest_wait_s)}
            </span>
          ) : (
            <span className="faint">
              {queue.closed_reason === "holiday" ? "วันหยุด" : "นอกเวลาทำการ"}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

// --- wrap-up (D45, D59) ---------------------------------------------------

export function WrapupPanel({
  presence,
  saved,
  onSave,
  onSaveAndDeclare,
  busy,
}: {
  presence: Presence;
  saved: boolean;
  onSave: (payload: Record<string, unknown>) => void;
  onSaveAndDeclare: (payload: Record<string, unknown>, intent: string) => void;
  busy: boolean;
}) {
  const [disposition, setDisposition] = useState("advice_given");
  const [notes, setNotes] = useState("");
  const [followUp, setFollowUp] = useState(false);
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
  const lastCallSpent = presence.intent_reason === "last_call_fulfilled";
  const draining = presence.agent_intent === "draining";
  const canOfferReady = presence.declarable.includes("ready") && !draining && !lastCallSpent;

  return (
    <div className="panel">
      <h2>สรุปหลังจบสาย</h2>
      {saved && <span className="badge ok">บันทึกแล้ว</span>}
      <p className="faint">
        การบันทึกจะปิด “บันทึกของสายนี้” เท่านั้น — งานหลังสายจะจบเมื่อคุณเลือกสถานะถัดไป
      </p>

      <div className="stack" style={{ marginTop: 8 }}>
        <select value={disposition} onChange={(e) => setDisposition(e.target.value)}>
          <option value="advice_given">ให้คำแนะนำแล้ว</option>
          <option value="claim_opened">เปิดเคลม</option>
          <option value="document_sent">ส่งเอกสาร</option>
          <option value="escalated">ส่งต่อ</option>
          <option value="callback_scheduled">นัดโทรกลับ</option>
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
          <button onClick={() => onSave(payload())} disabled={busy || saved}>
            บันทึก
          </button>
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
