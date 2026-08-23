/**
 * The panels. Presentational: they render what the server said and call back on click.
 *
 * The one rule worth stating loudly — **nothing here decides what may be shown.** The
 * brief arrives already rendered for the current assurance level (`D42`), so a locked
 * field is *absent from the payload*, not hidden by a conditional in this file. If you
 * ever find yourself writing `assurance >= L2 ? show : hide` in here, the gate has
 * quietly moved to the wrong side of the wire.
 */

import { useEffect, useState } from "react";
import type { Brief, Capture, Identity, Offer, Presence, Queue } from "./api";

const CHALLENGES = [
  { value: "date_of_birth", label: "วันเกิด" },
  { value: "citizen_id_last4", label: "เลขบัตรประชาชน 4 ตัวท้าย" },
  { value: "policy_number", label: "เลขกรมธรรม์" },
  { value: "recent_claim_amount", label: "ยอดเคลมล่าสุด" },
];

const INTENTS: { value: string; label: string }[] = [
  { value: "ready", label: "พร้อมรับสาย" },
  { value: "break", label: "พัก" },
  { value: "lunch", label: "พักกลางวัน" },
  { value: "training", label: "อบรม" },
  { value: "admin", label: "งานเอกสาร" },
  { value: "last_call", label: "สายสุดท้าย" },
  { value: "draining", label: "ไม่รับสายใหม่" },
];

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

function mmss(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** A ticker that runs in the client purely for display. Never a source of truth: the
 *  server owns every deadline, and a browser tab that was throttled in the background
 *  would otherwise "expire" an offer the server still considers live. */
function useTicker(active: boolean): number {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setTick((n) => n + 1), 250);
    return () => window.clearInterval(id);
  }, [active]);
  return Date.now();
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
  const now = useTicker(offer !== null);
  if (!offer) return <div className="scrim hidden" />;

  const elapsed = (now - Date.parse(offer.offered_at)) / 1000;
  const left = Math.max(0, offer.timeout_s - elapsed);

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
          {/* Counting down is the client's only clock, and it is decoration: when it hits
              zero the server has already decided (RONA) and will tell us. */}
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
          {presence.system_state}
        </span>
        <span className="badge">{presence.agent_intent}</span>
      </div>

      {presence.agent_intent === "not_ready" && (
        <p className="faint" style={{ marginTop: 8 }}>
          คุณเข้าสู่ระบบแล้วแต่ยังไม่ได้กด “พร้อมรับสาย” — ระบบจะไม่ส่งสายให้จนกว่าคุณจะกดเอง
        </p>
      )}

      <div className="stack" style={{ marginTop: 10 }}>
        <div className="row">
          {INTENTS.map((intent) => (
            <button
              key={intent.value}
              className={presence.agent_intent === intent.value ? "on" : ""}
              onClick={() => onDeclare(intent.value)}
              disabled={busy}
            >
              {intent.label}
            </button>
          ))}
        </div>
      </div>

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

// --- identity (D42) -------------------------------------------------------

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
  const [relationship, setRelationship] = useState("");

  if (!identity) {
    return (
      <div className="panel">
        <h2>ตัวตนผู้ติดต่อ</h2>
        <p className="faint">ยังไม่มีสายที่กำลังสนทนา</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h2>ตัวตนผู้ติดต่อ</h2>
      <AssuranceBadge assurance={identity.assurance} />
      <div className="faint" style={{ marginTop: 6 }}>
        {identity.may_disclose_policy_details
          ? "เปิดเผยรายละเอียดกรมธรรม์ได้"
          : "ยังเปิดเผยรายละเอียดกรมธรรม์ไม่ได้"}
      </div>

      {identity.authority_check_required && (
        <div className="rationale" style={{ borderLeftColor: "var(--warn)" }}>
          ผู้ติดต่อแจ้งว่าดำเนินการแทนเจ้าของกรมธรรม์ — ตรวจสอบสิทธิ์ในการดำเนินการก่อน
        </div>
      )}

      <div className="stack" style={{ marginTop: 12 }}>
        <div className="faint">ยืนยันด้วยคำถาม</div>
        <select value={challenge} onChange={(e) => setChallenge(e.target.value)}>
          {CHALLENGES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <button
          className="primary"
          disabled={busy || !callId}
          onClick={() => onAttest({ outcome: "confirmed", challenge })}
        >
          ยืนยันว่าใช่บุคคลนี้
        </button>

        <input
          placeholder="ความสัมพันธ์ เช่น ลูกสาว"
          value={relationship}
          onChange={(e) => setRelationship(e.target.value)}
        />
        <button
          disabled={busy || !callId}
          onClick={() => onAttest({ outcome: "third_party", relationship })}
        >
          เป็นผู้ดำเนินการแทน
        </button>

        <button
          className="danger"
          disabled={busy || !callId}
          onClick={() => onAttest({ outcome: "not_this_person" })}
        >
          ไม่ใช่บุคคลนี้
        </button>
        {/* Three outcomes, never two. Forcing a daughter calling for her father into
            "confirmed" would put a verification that never happened into the audit log,
            which is the only reason the log exists (`D42`). */}
      </div>
    </div>
  );
}

// --- keypad capture (D44) -------------------------------------------------

export function CapturePanel({
  capture,
  liveDigits,
  callId,
  onStart,
  onKey,
  onStop,
  onDiscard,
  onLookup,
  busy,
}: {
  capture: Capture | null;
  liveDigits: string;
  callId: string | null;
  onStart: () => void;
  onKey: (digit: string) => void;
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
        หรืออย่างอื่น ระบบไม่เดาว่าคืออะไร
      </p>

      <div className="digits mono">{open ? liveDigits || "…" : (capture?.masked ?? "")}</div>

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
          <div className="faint">ตรวจสอบว่าเลขนี้ตรงกับอะไร (ไม่เปลี่ยนระดับการยืนยันตัวตน)</div>
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
            </div>
          ))}
          {capture.lookups.length > 0 && (
            <div className="faint">
              ผลการเทียบเป็นเพียงหลักฐาน ไม่ใช่การยืนยันตัวตน — ต้องกดยืนยันเองที่แผงด้านบน
            </div>
          )}
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
                {brief.other_policy_count > 0
                  ? ` · อีก ${brief.other_policy_count} ฉบับ`
                  : ""}
              </div>
            </>
          ) : (
            /* Not "hidden": the server did not send it. The row says so rather than
               rendering an empty value that looks like missing data (`D42`). */
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
            สิ่งที่ควรทำ
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
          <span className={`dot`} style={{ color: queue.is_open ? "var(--ok)" : "var(--bad)" }} />
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

// --- wrap-up (D45) --------------------------------------------------------

export function WrapupPanel({
  callId,
  acwSeconds,
  longAcw,
  saved,
  onSave,
  onSaveAndReady,
  busy,
}: {
  callId: string | null;
  acwSeconds: number | null;
  longAcw: boolean;
  saved: boolean;
  onSave: (payload: Record<string, unknown>) => void;
  onSaveAndReady: (payload: Record<string, unknown>) => void;
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

  return (
    <div className="panel">
      <h2>สรุปหลังจบสาย</h2>
      <div className="row">
        <span className={`badge ${longAcw ? "warn" : "info"}`}>
          ACW {acwSeconds === null ? "—" : mmss(acwSeconds)}
        </span>
        {saved && <span className="badge ok">บันทึกแล้ว</span>}
      </div>
      <p className="faint">
        เวลานี้เริ่มนับตั้งแต่วางสาย และจะหยุดเมื่อคุณเลือกสถานะถัดไปเท่านั้น —
        ระบบไม่ตั้งเป็นพร้อมรับสายให้เอง
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
          <button onClick={() => onSave(payload())} disabled={busy || !callId || saved}>
            บันทึก
          </button>
          {/* Two requests behind one button, deliberately (`D45`): saving closes the call
              RECORD, declaring ends after-call work, and either may happen alone. */}
          <button
            className="primary"
            onClick={() => onSaveAndReady(payload())}
            disabled={busy || !callId}
          >
            บันทึกแล้วพร้อมรับสาย
          </button>
        </div>
      </div>
    </div>
  );
}

export { mmss };
