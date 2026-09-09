/** The configurable test call (`D132`).
 *
 * `+ สายทดสอบ` places a caller with sensible defaults and always has. The problem it left
 * is that the interesting halves of this system — the transcript arriving while somebody
 * waits, and the AI summary landing on the offer card before Accept — could only be
 * reached with a `curl` line buried in the README. A feature nobody on the team can press
 * is a feature nobody on the team can rehearse, and this project has shipped that mistake
 * before (`D121`: the tool rail worked for a fortnight with no control anywhere).
 *
 * So there are two buttons. The plain one is unchanged, because a one-click caller is what
 * you want ninety times out of a hundred. The second opens this dialog, which starts on
 * exactly the plain button's defaults — **press place with nothing touched and you get the
 * same call** — and lets every stage be turned on deliberately.
 *
 * ⚠️ **Two things are reported rather than offered, and that is the design.**
 *
 * *Assurance* is derived from evidence (`D20`): a number nobody holds is L0, a number the
 * core holds is L1. There is no control for it, because a control would be asserting a
 * level with nothing behind it — the thing `D84` deleted from the IVR. Pick a caller and
 * the dialog tells you which level that produces.
 *
 * *Urgency* comes from the intent, out of `config/intents.yaml` (`D28`). Pick a reason and
 * the dialog shows the urgency it carries. Neither is a knob, and pretending otherwise
 * would teach the person driving it something false about how the system decides.
 */

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./api";
import type { CallOptions } from "./api";

function Pill({
  selected,
  disabled,
  onSelect,
  title,
  className = "",
  children,
}: {
  selected: boolean;
  disabled?: boolean;
  onSelect: () => void;
  title?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      disabled={disabled}
      title={title}
      className={`pill-opt ${selected ? "on" : ""} ${className}`.trim()}
      onClick={onSelect}
    >
      {children}
    </button>
  );
}

/** A labelled tick. `D129`'s argument applies to checkboxes too — a native box beside a
 *  Thai label sits on the first line's baseline while the label wraps under it — but a
 *  checkbox is not one-of-many, so it keeps `role="checkbox"` rather than radio. */
function Check({
  on,
  disabled,
  onToggle,
  label,
  hint,
}: {
  on: boolean;
  disabled?: boolean;
  onToggle: () => void;
  label: string;
  hint?: string;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={on}
      disabled={disabled}
      className={`pill-opt wide ${on ? "on" : ""}`}
      onClick={onToggle}
      title={hint}
    >
      <span style={{ marginRight: 8 }}>{on ? "☑" : "☐"}</span>
      {label}
      {hint && (
        <div className="faint" style={{ marginTop: 3, whiteSpace: "normal" }}>
          {hint}
        </div>
      )}
    </button>
  );
}

export function TestCallDialog({
  open,
  onClose,
  onPlaced,
  defaultIntent,
  skills,
  offerable,
}: {
  open: boolean;
  onClose: () => void;
  onPlaced: () => void;
  /** The intent the plain button would have used, so the dialog opens on its defaults. */
  defaultIntent: string;
  /** This agent's own skills, so the dialog can say which reasons could actually reach
   *  them. Both halves are server facts — the agent's skills and the intent's required
   *  skill — so this renders a decision rather than making one. */
  skills: { skill_code: string }[];
  /** Whether this agent could receive the call right now. Not a gate — the dialog says
   *  what will happen and lets you place it anyway, because placing while un-ready is how
   *  you watch the transcript arrive BEFORE the desk rings (`D21`, `D131`). */
  offerable: boolean;
}) {
  const [options, setOptions] = useState<CallOptions | null>(null);
  const [callerKey, setCallerKey] = useState("unknown");
  const [intent, setIntent] = useState(defaultIntent);
  const [waited, setWaited] = useState(40);
  const [ignoreHours, setIgnoreHours] = useState(true);
  const [record, setRecord] = useState(false);
  const [audio, setAudio] = useState<string | null>(null);
  const [realtime, setRealtime] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetched when the dialog OPENS, not on mount: on mount the agent has not signed in and
  // this 401s into a permanently empty dialog (`B32`, which cost a shift of an empty tool
  // rail). Keyed to `open` so it also refreshes if config changed under a long session.
  useEffect(() => {
    if (!open) return;
    let live = true;
    setError(null);
    api
      .callOptions()
      .then((o) => {
        if (!live) return;
        setOptions(o);
        setCallerKey(o.defaults.caller_key);
        setWaited(o.defaults.waited_s);
        setIgnoreHours(o.defaults.ignore_hours);
        setRecord(o.defaults.record);
        setAudio(o.defaults.audio);
        setRealtime(o.defaults.audio_realtime);
        setIntent(defaultIntent);
      })
      .catch((e) => live && setError(String(e)));
    return () => {
      live = false;
    };
  }, [open, defaultIntent]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const caller = options?.callers.find((c) => c.key === callerKey) ?? null;
  const chosen = options?.intents.find((i) => i.code === intent) ?? null;
  const hasAudio = (options?.audio.length ?? 0) > 0;

  // Recording without audio is legitimate — the caller consents and simply says nothing,
  // which is a real and common outcome (`D88`: `declined` and `ignored` are different
  // facts). It just produces no transcript, so the dialog says so rather than blocking it.
  const willTranscribe = record && Boolean(audio);
  const willSummarise = willTranscribe && Boolean(options?.llm.real);

  // ⚠️ The scripted engine always says the same thing whatever the caller chose, so it can
  // be a motor crash filed under a health claim. A good model then correctly answers
  // `is_clear: false` and refuses (`D119`) — which renders as NO AI summary and looks like
  // a broken feature. Warn rather than block: placing the mismatch on purpose is a fair
  // way to SEE the refusal working, which is itself worth demonstrating.
  const scriptIntent = options?.stt.script_intent ?? null;
  const scriptMismatch =
    willSummarise && scriptIntent !== null && scriptIntent !== intent;

  // ⚠️ Since `D117` an agent is NOT interchangeable with another on the same line: advice
  // and service are separate skills, and `claims.assist` is cross-line. So a motor
  // *advisor* placing a motor *claim* is never offered it — the caller simply waits in the
  // queue with `no_qualified_agent` and the screen says nothing. `D117`'s own note says
  // that cost about an hour to recognise the first time, and this dialog is the easiest
  // possible way to walk into it, so it is called out here rather than left to be
  // rediscovered.
  const held = new Set(skills.map((s) => s.skill_code));
  const unreachable = chosen !== null && !held.has(chosen.skill);

  const submit = async () => {
    if (!caller) return;
    setPending(true);
    setError(null);
    try {
      await api.placeCall({
        intent_code: intent,
        caller_number: caller.caller_number,
        waited_s: waited,
        ignore_hours: ignoreHours,
        // `intake_keys` is the offer's own script, separate from the menu's (`D88`):
        // "1" takes the recording, "2" declines it, and [] says nothing at all.
        intake_keys: record ? ["1"] : ["2"],
        audio: willTranscribe ? audio : null,
        audio_realtime: willTranscribe ? realtime : false,
      });
      onPlaced();
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <div
      className={open ? "scrim" : "scrim hidden"}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="transfer-dialog">
        <div className="queue-head">
          <h2>สายทดสอบแบบกำหนดเอง</h2>
          <span className="faint">คลิกนอกกรอบเพื่อปิด</span>
        </div>

        {error && <div className="rationale" style={{ borderLeftColor: "var(--bad)" }}>{error}</div>}
        {!options && !error && <div className="faint">กำลังโหลดตัวเลือก…</div>}

        {options && (
          <>
            <h3>ใครโทรมา</h3>
            <div className="pill-grid" role="radiogroup" aria-label="ผู้โทร">
              {options.callers.map((c) => (
                <Pill key={c.key} selected={callerKey === c.key} onSelect={() => setCallerKey(c.key)}>
                  {c.label_th}
                </Pill>
              ))}
            </div>
            {caller && (
              <div className="rationale" style={{ borderLeftColor: "var(--info)" }}>
                <div>
                  <strong>{caller.caller_number}</strong> → ระดับความเชื่อมั่น{" "}
                  <strong>{caller.assurance}</strong>
                </div>
                <div className="faint">{caller.note_th}</div>
                {/* The ladder is a consequence, not a setting. Saying so here is the
                    cheapest place this system ever gets to explain `D20`. */}
                <div className="faint" style={{ marginTop: 4 }}>
                  เบอร์โทรพิสูจน์ได้แค่ “น่าจะใช่” — ระดับ L2/L3 ต้องมาจากแอป
                  หรือจากที่เจ้าหน้าที่ยืนยันเอง (`D20`, `D44`)
                </div>
                {/* ⚠️ `Q38`. At L0 there is no customer, so no context snapshot, so no
                    brief — and the AI summary is rendered as part of the brief. The
                    verbatim transcript still arrives (`D106`), which makes this an
                    inconsistency rather than a policy, but it is what the code does
                    today and somebody choosing this caller should know before they
                    conclude the summary is broken. */}
                {caller.key === "unknown" && (
                  <div className="faint" style={{ marginTop: 4 }}>
                    ⚠️ ที่ระดับ L0 ยังไม่มีลูกค้าให้ประกอบข้อมูล จึง<strong>ไม่มีสรุป</strong>
                    ทั้งแบบกฎและแบบ AI — บทสนทนาดิบยังขึ้นตามปกติ ถ้าอยากเห็นสรุป
                    ให้เลือกลูกค้าที่ระบบรู้จัก
                  </div>
                )}
              </div>
            )}

            <h3>โทรมาเรื่องอะไร</h3>
            <select
              value={intent}
              onChange={(e) => setIntent(e.target.value)}
              style={{ width: "100%" }}
            >
              {options.intents.map((i) => (
                <option key={i.code} value={i.code}>
                  {held.has(i.skill) ? "" : "⚠ "}
                  {i.label_th} — {i.code}
                </option>
              ))}
            </select>
            {unreachable && chosen && (
              <div className="rationale" style={{ borderLeftColor: "var(--warn)", marginTop: 8 }}>
                ⚠️ เรื่องนี้ต้องใช้ทักษะ <code>{chosen.skill}</code> ซึ่งคุณไม่มี —
                สายจะเข้าคิวแล้ว<strong>ค้างอยู่อย่างนั้น</strong> เพราะไม่มีใครรับได้
                <div className="faint" style={{ marginTop: 4 }}>
                  ตั้งแต่ <code>D117</code> ที่ปรึกษากับฝ่ายบริการเป็นคนละทักษะ และงานเคลม
                  (<code>claims.assist</code>) เป็นทักษะข้ามสายผลิตภัณฑ์ — เลือกเรื่องที่ตรง
                  กับทักษะของคุณ หรือให้เจ้าหน้าที่อีกคนเข้าระบบมารับ
                </div>
              </div>
            )}
            {chosen && (
              <div className="faint" style={{ marginTop: 6 }}>
                สาย <strong>{chosen.line}</strong> · ทักษะ <strong>{chosen.skill}</strong> ·
                ความเร่งด่วน <strong>{chosen.urgency}</strong>
                {chosen.handoff_to_insurer && " · เป็นเรื่องที่ต้องส่งต่อบริษัทประกัน"}
                <div style={{ marginTop: 3 }}>
                  ความเร่งด่วนมาจาก <code>config/intents.yaml</code> ไม่ใช่ตัวเลือกในนี้
                </div>
              </div>
            )}

            <h3>ระหว่างรอสาย</h3>
            <div className="pill-grid">
              <Check
                on={record}
                onToggle={() => setRecord(!record)}
                label="ลูกค้ากดรับข้อเสนอให้เล่าเรื่องไว้ก่อน (กด 1)"
                hint="ไม่ติ๊ก = กด 2 คือรอสายเฉยๆ ซึ่งก็เป็นผลลัพธ์ที่ถูกต้องเหมือนกัน"
              />
              <Check
                on={Boolean(audio)}
                disabled={!record || !hasAudio}
                onToggle={() => setAudio(audio ? null : (options.audio[0] ?? null))}
                label="เล่นไฟล์เสียงจริงลงสาย (ได้ transcript)"
                hint={
                  !hasAudio
                    ? `ไม่มีไฟล์ใน ${options.audio_dir} — สร้างด้วย scripts/make_demo_audio.py`
                    : !record
                      ? "ต้องติ๊กข้อบนก่อน — ไม่ยินยอมก็ไม่บันทึก"
                      : options.stt.note_th
                }
              />
              {record && audio && options.audio.length > 1 && (
                <select value={audio} onChange={(e) => setAudio(e.target.value)}>
                  {options.audio.map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
              )}
              <Check
                on={realtime}
                disabled={!willTranscribe}
                onToggle={() => setRealtime(!realtime)}
                label="เล่นตามเวลาจริง (ช้ากว่า แต่เห็นข้อความไหลเข้าทีละประโยค)"
                hint="ไม่ติ๊ก = ป้อนรวดเดียว ซึ่งเร็วแต่ไม่เหมือนสายจริง"
              />
            </div>

            <h3>เงื่อนไขคิว</h3>
            <div className="pill-grid">
              <Check
                on={ignoreHours}
                onToggle={() => setIgnoreHours(!ignoreHours)}
                label="ข้ามเวลาทำการ"
                hint="ซ้อมกันตอนตีสอง คิวส่วนใหญ่ปิด (`D54`)"
              />
            </div>
            <div style={{ marginTop: 8 }}>
              <label className="faint">รอมาแล้ว {Math.round(waited)} วินาที</label>
              <input
                type="range"
                min={0}
                max={300}
                step={10}
                value={waited}
                onChange={(e) => setWaited(Number(e.target.value))}
                style={{ width: "100%" }}
              />
              <div className="faint">
                ยิ่งรอนาน ยิ่งเร่งด่วนขึ้นตาม <code>wait_pressure</code> — และเกินเพดานของ
                ระดับความเร่งด่วนนั้นจะถูกจับคู่ให้ทันที (`D93`, `D94`)
              </div>
            </div>

            {/* What this call will actually exercise, said plainly. A checkbox that
                silently does nothing is worse than one greyed out with its reason
                (`D71`), and "is the AI on?" is the single most common question here. */}
            {!offerable && (
              <div className="rationale" style={{ borderLeftColor: "var(--info)", marginTop: 8 }}>
                คุณยังไม่ได้กด <strong>พร้อมรับสาย</strong> — สายนี้จะไปรอในคิวจนกว่าคุณจะกด
                <div className="faint" style={{ marginTop: 4 }}>
                  ซึ่งเป็นวิธีที่ดีที่สุดในการดูระบบทำงาน: ปล่อยให้ลูกค้าเล่าเรื่องจนจบก่อน
                  แล้วค่อยกดพร้อมรับสาย จะได้เห็นบทสนทนาและสรุปจาก AI ขึ้นบนการ์ดเรียกสาย
                  <strong>ก่อน</strong> ที่จะกดรับ
                </div>
              </div>
            )}

            <h3>สายนี้จะได้ทดสอบอะไรบ้าง</h3>
            <div className="rationale">
              <div>
                {willTranscribe ? "✅" : "⬜"} ถอดเสียงเป็นข้อความ — {options.stt.note_th}
              </div>
              <div style={{ marginTop: 4 }}>
                {willSummarise ? "✅" : "⬜"} ให้โมเดลสรุป — {options.llm.note_th}
              </div>
              {willSummarise && (
                <div className="faint" style={{ marginTop: 4 }}>
                  สรุประหว่างรอรับสายจะขึ้นบนการ์ดเรียกสาย <strong>ก่อน</strong> กดรับ
                  แล้วถูกแทนที่ด้วยฉบับเต็มหลังกดรับ (`D131`)
                </div>
              )}
              {scriptMismatch && (
                <div className="rationale" style={{ borderLeftColor: "var(--warn)", marginTop: 8 }}>
                  ⚠️ บทพูดชุดสาธิตเป็นเรื่อง <code>{scriptIntent}</code> แต่คุณเลือก{" "}
                  <code>{intent}</code> — โมเดลจะมองว่าข้อมูลไม่สอดคล้องและ
                  <strong>ปฏิเสธที่จะสรุป</strong> ซึ่งถูกต้องตามที่ออกแบบไว้ (`D119`)
                  แต่บนหน้าจอจะเห็นเป็น “ไม่มีสรุปจาก AI”
                  <div className="faint" style={{ marginTop: 4 }}>
                    อยากเห็นสรุปจริง ให้เลือก <code>{scriptIntent}</code> · อยากเห็นการปฏิเสธ
                    ก็ปล่อยไว้แบบนี้
                  </div>
                </div>
              )}
              {record && !audio && (
                <div className="faint" style={{ marginTop: 4 }}>
                  ลูกค้ายินยอมแต่ไม่ได้พูดอะไร — จะไม่มี transcript และไม่มีสรุป
                  ซึ่งเป็นผลลัพธ์ที่ถูกต้อง ไม่ใช่ความผิดพลาด
                </div>
              )}
            </div>

            <div className="row" style={{ marginTop: 16, justifyContent: "flex-end", gap: 8 }}>
              <button onClick={onClose}>ยกเลิก</button>
              <button className="primary" disabled={pending || !caller} onClick={submit}>
                {pending ? "กำลังวางสาย…" : "เพิ่มเข้าคิว"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
