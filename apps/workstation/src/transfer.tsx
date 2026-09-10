/**
 * Where the call goes when it is not staying with this broker (`D124`).
 *
 * `D117` split the duties from Krungsri's own slide: the insurer underwrites, rules on
 * coverage and **pays claims**; the broker analyses needs, selects the plan *and the
 * company*, services the policy and chases renewals. Since `D117` a claim call arrives
 * with a banner above the policy panel saying it ends with the insurer — and until now
 * the banner was the whole feature. The broker read it, said the sentence, pressed
 * วางสาย, and typed what they remembered into the wrap-up.
 *
 * This is the other half: **who** it went to, **why**, and a disposition they do not have
 * to compose.
 *
 * Three rules, all inherited:
 *
 * 1. **The options are served, never built here** (`D72`, `D121`). Carriers, reasons and
 *    which reasons are available all come from the server, because two of the inputs —
 *    the carrier on this customer's own policy, and whether we hold a policy at all —
 *    are facts the client cannot work out.
 * 2. **The client names WHICH, never WHAT IT IS CALLED.** A code, or the flag meaning
 *    "the carrier on their policy". A request able to supply a company name could file a
 *    handoff to a company that never wrote anything for this customer.
 * 3. **Greying is the courtesy; the refusal is the gate** (`D121`). A reason needing a
 *    policy renders disabled with its reason attached, and the server refuses it
 *    independently.
 *
 * ⚠️ The second tab is **half built, on purpose** (`D141`). Everything up to the button is
 * real — the roster, the live presence, the filtering, the ranking — because all of that
 * is a READ, and the matcher already answers it on every tick. What is missing is the one
 * thing that changes state: `D63`'s consulted transfer needs a transfer offer distinct
 * from a queue offer plus a rework of "which call is mine" inside `services/agents/`,
 * which is where `B7`, `B25` and `B28` all lived.
 *
 * So the final button is disabled and says why, which is `D115`'s labelled-stub rule
 * applied to the *action* rather than to the whole feature. The screen shows the design
 * working on real data; it just cannot commit it.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./api";
import type { InternalTransferOptions } from "./api";
import type { HandoffOptions } from "./api";

/**
 * A radio that looks like a pill.
 *
 * The native `<input type="radio">` this replaces drew its own bullet, and a bullet beside
 * a variable-length Thai label never lines up — the marker sits on the first line's
 * baseline while the label wraps under it. Worse, each option owned a full-width row, so
 * six short reasons produced a tall column of mostly empty space.
 *
 * `role="radio"` + `aria-checked` keeps the semantics the markup lost: a screen reader
 * still hears one-of-many, and arrow keys still move between them, because the group is a
 * `radiogroup` and only the selected pill is in the tab order.
 */
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

export function TransferButton({
  disabled,
  onOpen,
}: {
  disabled: boolean;
  onOpen: () => void;
}) {
  return (
    <button className="warn" disabled={disabled} onClick={onOpen} title="ส่งต่อหรือโอนสาย">
      ส่งต่อ
    </button>
  );
}

/** How a candidate's state reads at a glance. Colour is never the only signal — the word
 *  is there too, because a dot alone is unreadable to anyone who cannot separate them. */
const STATE_TH: Record<string, { label: string; cls: string }> = {
  available: { label: "ว่าง", cls: "ok" },
  on_call: { label: "กำลังคุยสาย", cls: "warn" },
  after_call_work: { label: "สรุปงานหลังสาย", cls: "warn" },
  offering: { label: "กำลังมีสายเรียก", cls: "warn" },
  offline: { label: "ออฟไลน์", cls: "bad" },
};

/** Why the matcher ruled somebody out, in the broker's words (`D50`). Never a bare
 *  "unavailable": "they lack the skill" and "they are on another call" are different
 *  conversations, and the second one is worth waiting for. */
const BLOCKED_TH: Record<string, string> = {
  skill: "ไม่มีทักษะที่สายนี้ต้องการ",
  language: "ระดับภาษาไม่ถึงเกณฑ์ของสายนี้",
  offline: "ออฟไลน์",
  not_ready: "ยังไม่กดพร้อมรับสาย",
  busy: "ติดสายอื่นอยู่",
  at_capacity: "รับสายเต็มจำนวนแล้ว",
  already_offered: "เคยถูกเสนอสายนี้แล้ว",
};

/**
 * `D141`. The roster, the filtering and the ranking are REAL — same `hard_filter` and
 * `score_fit` the matcher runs — and the transfer button is not.
 */
function InternalTransferTab({
  open,
  callId,
  busy,
}: {
  open: boolean;
  callId: string | null;
  busy: boolean;
}) {
  const [data, setData] = useState<InternalTransferOptions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  /** `null` means "the call's own skill" — resolved by the server, so the client never
   *  has to know what the call routed on before the first response (`D146`). */
  const [skill, setSkill] = useState<string | null>(null);
  /* ⚠️ Defaults to showing EVERYONE, reasons included. The first build defaulted this on
     and rendered "nobody matches" on a floor where thirteen people were simply not signed
     in — which is `D50`'s exact complaint: a bare "unavailable" hides the answer to the
     question the broker is actually asking. Seen by opening the tab. */
  const [eligibleOnly, setEligibleOnly] = useState(false);

  /* `B32`: keyed to the precondition, not to mount. This tab is inside a dialog that
     exists before it is shown, and fetching on mount would run before there is a call. */
  const load = useCallback(async () => {
    if (!callId) return;
    try {
      setData(await api.internalTransferOptions(callId, skill ?? undefined));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "โหลดรายชื่อไม่สำเร็จ");
    }
  }, [callId, skill]);

  /* ⚠️ **The roster is LIVE while the dialog is open** (`D146`). Presence changes from
   * under this screen constantly — somebody signs in, finishes a call, goes on break —
   * and a list fetched once tells the broker to transfer to a desk that went offline
   * thirty seconds ago. It used to update only when the dialog was closed and reopened.
   *
   * ⚠️ The interval holds `load` in a REF and depends on nothing that changes per render.
   * The workstation re-renders **once a second** to drive its timers, so an effect
   * depending on a callback identity is torn down and rebuilt before a 4-second interval
   * can ever fire — which is `B33` exactly, and it looked like the server not answering. */
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    if (!open || !callId) return;
    void loadRef.current();
    const id = window.setInterval(() => void loadRef.current(), 4000);
    return () => window.clearInterval(id);
  }, [open, callId, skill]);

  if (!callId) return <div className="faint">ไม่มีสายที่กำลังคุยอยู่</div>;
  if (error) {
    return (
      <div className="rationale" style={{ borderLeftColor: "var(--bad)" }}>
        {error}
      </div>
    );
  }
  if (!data) return <div className="faint">กำลังโหลดรายชื่อ…</div>;

  const term = query.trim().toLowerCase();
  const rows = data.agents.filter((a) => {
    if (eligibleOnly && !a.eligible) return false;
    if (!term) return true;
    return (
      a.display_name.toLowerCase().includes(term) ||
      a.agent_id.toLowerCase().includes(term) ||
      a.skills.some((s) => s.skill_code.toLowerCase().includes(term))
    );
  });

  return (
    <>
      {/* ⚠️ Said FIRST, before the list. A screen that looks operable and refuses at the
          end wastes the reader's time; `D71`'s rule is that a control says why before it
          is pressed, not after. */}
      <div className="rationale" style={{ borderLeftColor: "var(--warn)" }}>
        <b>ยังโอนสายจริงไม่ได้</b> — {data.not_implemented_th}
      </div>

      <h3>
        เจ้าหน้าที่ที่รับสายนี้ได้
        <span className="faint">
          {" "}
          · ต้องมีทักษะ <code>{data.required_skill ?? "—"}</code> · ผ่านเกณฑ์{" "}
          {data.eligible_count} คน
        </span>
      </h3>

      {/* `D146`. Defaults to the call's own skill and is NOT a ceiling: a caller mis-keyed
          the menu, or the conversation turned out to be about something else, and those
          are two of the commonest reasons a transfer happens at all. */}
      <div className="filter-row">
        <label className="faint" style={{ flex: "0 0 auto" }}>
          ทักษะที่ต้องการ{" "}
          <select
            value={skill ?? ""}
            disabled={busy}
            onChange={(e) => {
              setSkill(e.target.value || null);
              setPicked(null);
            }}
          >
            <option value="">
              ตามสายนี้
              {data.call_skill ? ` (${data.call_skill})` : ""}
            </option>
            {data.skills.map((s) => (
              <option key={s.skill_code} value={s.skill_code}>
                {s.label_th}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="filter-row">
        <input
          type="search"
          placeholder="ค้นหาชื่อ รหัส หรือทักษะ"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <label className="faint">
          <input
            type="checkbox"
            checked={eligibleOnly}
            onChange={(e) => setEligibleOnly(e.target.checked)}
          />{" "}
          เฉพาะคนที่รับได้
        </label>
      </div>

      {/* `D63`'s "let the system choose", answered by the real scorer rather than by a
          shrug. It is the top of the list because that is what the solver would pick. */}
      {data.system_choice && (
        <button
          type="button"
          role="radio"
          aria-checked={picked === data.system_choice}
          className={`pill-opt wide ${picked === data.system_choice ? "on" : ""}`}
          onClick={() => setPicked(data.system_choice)}
        >
          <b>ให้ระบบเลือกให้</b>
          <span className="pill-sub">
            ตอนนี้คือ {data.agents.find((a) => a.agent_id === data.system_choice)?.display_name} —
            คะแนนความเหมาะสมสูงสุดจากตัวให้คะแนนชุดเดียวกับที่ใช้จัดสาย
          </span>
        </button>
      )}

      <ul className="roster">
        {rows.map((a) => {
          const state = STATE_TH[a.system_state] ?? { label: a.system_state, cls: "" };
          return (
            <li key={a.agent_id}>
              <button
                type="button"
                role="radio"
                aria-checked={picked === a.agent_id}
                /* Greying is the courtesy; the SERVER is the gate (`D121`, `B5`). */
                disabled={busy || !a.eligible}
                className={`roster-row ${picked === a.agent_id ? "on" : ""}`}
                onClick={() => setPicked(a.agent_id)}
              >
                <span className="roster-name">
                  <b>{a.display_name}</b>
                  <span className="faint mono"> {a.agent_id}</span>
                </span>
                <span className={`chip ${state.cls}`}>{state.label}</span>
                <span className="faint">
                  {a.current_load}/{a.max_concurrent} สาย
                </span>
                {a.eligible ? (
                  <span className="chip ok">เหมาะสม {a.fit?.toFixed(2)}</span>
                ) : (
                  <span className="chip bad">
                    {BLOCKED_TH[a.blocked_by ?? ""] ?? a.blocked_by}
                  </span>
                )}
                <span className="faint roster-skills">
                  {a.skills.map((s) => s.skill_code).join(" · ")}
                </span>
              </button>
            </li>
          );
        })}
        {rows.length === 0 && (
          <li className="faint">ไม่มีใครตรงเงื่อนไข — ลองเอาตัวกรอง “เฉพาะคนที่รับได้” ออก</li>
        )}
      </ul>

      {/* ⚠️ `can_execute` comes from the SERVER, so the client cannot decide to enable a
          button whose endpoint does not exist. When `D63` lands, the server flips it and
          this button starts working without a second decision being made here. */}
      <button className="primary" disabled={!data.can_execute || !picked} title={data.not_implemented_th}>
        โอนสายให้เจ้าหน้าที่คนนี้ {data.can_execute ? "" : "(ยังไม่เปิดใช้งาน)"}
      </button>
      <p className="faint" style={{ marginTop: 8 }}>
        เมื่อเปิดใช้งานแล้ว ลูกค้าจะยัง<b>คุยกับคุณอยู่</b>จนกว่าอีกฝ่ายจะกดรับและคุณกดปล่อยสายเอง
        ไม่ใช่ตอนที่อีกฝ่ายกดรับ (<code>D63</code>) — เป็นการโอนแบบ “ปรึกษาก่อนโอน”
        ไม่ใช่โยนสายทิ้งไว้
      </p>
    </>
  );
}

export function TransferDialog({
  open,
  options,
  busy,
  callId,
  onClose,
  onHandOff,
}: {
  open: boolean;
  options: HandoffOptions | null;
  busy: boolean;
  /** `D141`. The internal tab fetches its own roster, so it needs the call it is about. */
  callId: string | null;
  onClose: () => void;
  onHandOff: (body: {
    reason_code: string;
    insurer_code?: string | null;
    use_policy_insurer?: boolean;
    note?: string;
  }) => void;
}) {
  const [tab, setTab] = useState<"external" | "internal">("external");
  const [target, setTarget] = useState<string>("");
  const [reason, setReason] = useState<string>("");
  const [note, setNote] = useState("");

  // Reset when the dialog opens rather than when it closes: a form that keeps the last
  // call's answers is one wrong click from filing a handoff about somebody else.
  useEffect(() => {
    if (!open) return;
    setTab("external");
    setNote("");
    setTarget(options?.policy_insurer ? "__policy__" : "");
    setReason("");
  }, [open, options?.policy_insurer]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const chosenReason = options?.reasons.find((r) => r.code === reason) ?? null;
  const ready = Boolean(target && chosenReason?.available);

  const submit = () => {
    if (!ready || !chosenReason) return;
    onHandOff({
      reason_code: chosenReason.code,
      use_policy_insurer: target === "__policy__",
      insurer_code: target === "__policy__" ? null : target,
      note,
    });
  };

  // `.scrim.hidden` rather than unmounting, and the backdrop closes it —
  // `e.target === e.currentTarget` is load-bearing, or a click inside bubbles out and
  // closes the dialog mid-form (`D122`'s amendment, learned on the tool box).
  return (
    <div
      className={open ? "scrim" : "scrim hidden"}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="transfer-dialog">
        <div className="queue-head">
          <h2>ส่งต่อสายนี้</h2>
          <span className="faint">คลิกนอกกรอบเพื่อปิด</span>
        </div>

        <div className="tabs">
          <button
            className={tab === "external" ? "on" : ""}
            onClick={() => setTab("external")}
          >
            ส่งต่อบริษัทประกัน
          </button>
          <button
            className={tab === "internal" ? "on" : ""}
            onClick={() => setTab("internal")}
          >
            โอนสายภายใน
          </button>
        </div>

        {tab === "external" ? (
          <>
            {options?.handoff_expected && (
              <p className="banner warn">
                เรื่องนี้เป็นอำนาจของบริษัทประกันตามที่แจ้งไว้ก่อนรับสาย — เราช่วยรวบรวมข้อมูลและประสานงานให้
              </p>
            )}

            <h3>ส่งต่อให้บริษัทใด</h3>
            {/* The customer's own carrier is its own pill above the market grid, not a row
                in it: handing a claim to a company that did not write the policy is not a
                handoff, it is a wrong number. */}
            {options?.policy_insurer && (
              <Pill
                selected={target === "__policy__"}
                disabled={busy}
                onSelect={() => setTarget("__policy__")}
                className="wide"
              >
                <b>{options.policy_insurer}</b>
                <span className="pill-sub">บริษัทที่รับประกันกรมธรรม์ของลูกค้า</span>
              </Pill>
            )}
            <div className="pill-grid" role="radiogroup" aria-label="บริษัทที่จะส่งต่อ">
              {options?.insurers.map((ins) => (
                <Pill
                  key={ins.code}
                  selected={target === ins.code}
                  disabled={busy}
                  onSelect={() => setTarget(ins.code)}
                >
                  {ins.name_th}
                </Pill>
              ))}
            </div>

            <h3>เพราะอะไร</h3>
            <div className="pill-grid" role="radiogroup" aria-label="เหตุผลในการส่งต่อ">
              {options?.reasons.map((r) => (
                <Pill
                  key={r.code}
                  selected={reason === r.code}
                  disabled={busy || !r.available}
                  // Greying is the courtesy, the server is the gate (`D121`) — so the
                  // reason travels on the control the broker can actually hover.
                  title={
                    r.available
                      ? undefined
                      : "เหตุผลนี้ใช้กับกรมธรรม์ที่ลูกค้าถืออยู่ — สายนี้ไม่มีกรมธรรม์ที่เรามองเห็น"
                  }
                  onSelect={() => setReason(r.code)}
                >
                  {r.label_th}
                </Pill>
              ))}
            </div>

            <label className="field">
              <span className="faint">บันทึกเพิ่มเติม (ไม่บังคับ)</span>
              <textarea
                rows={2}
                value={note}
                maxLength={500}
                onChange={(e) => setNote(e.target.value)}
                disabled={busy}
              />
            </label>

            <div className="row">
              <span className="faint">
                กดแล้วสายจะจบลงและเข้าสู่งานหลังจบสาย พร้อมข้อความสรุปที่กรอกไว้ให้
              </span>
              <div className="spacer" />
              <button className="primary" disabled={!ready || busy} onClick={submit}>
                ส่งต่อและวางสาย
              </button>
            </div>
          </>
        ) : (
          <InternalTransferTab open={open} callId={callId} busy={busy} />
        )}
      </div>
    </div>
  );
}
