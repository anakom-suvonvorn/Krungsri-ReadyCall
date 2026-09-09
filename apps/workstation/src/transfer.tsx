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
 * ⚠️ The second tab is **not built** and says so. `D63` designed the internal consulted
 * transfer at P2b — one filtered roster, live presence per candidate, the caller moving
 * last on a deliberate press by whoever is talking to them — and it needs a transfer
 * offer distinct from a queue offer, which is where the work is. A labelled stub is the
 * rule for exactly this (`D115`): it shows the design without claiming it runs.
 */

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
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

export function TransferDialog({
  open,
  options,
  busy,
  onClose,
  onHandOff,
}: {
  open: boolean;
  options: HandoffOptions | null;
  busy: boolean;
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
          // A labelled stub, which is the rule for anything that looks real and is not
          // (`D115`). The design is `D63`'s and is written down; what is missing is a
          // transfer offer distinct from a queue offer, because the caller must keep
          // talking to the first broker while the second one decides.
          <div className="stub-panel">
            <p>
              <b>ยังไม่เปิดใช้งาน</b> — โอนสายให้เจ้าหน้าที่คนอื่นในทีม
            </p>
            <p className="faint">
              ออกแบบไว้แล้ว (<code>D63</code>): เลือกจากรายชื่อที่กรองด้วยทักษะและภาษา
              พร้อมสถานะจริงของแต่ละคน หรือให้ระบบเลือกให้ตามความเหมาะสม —
              ลูกค้าจะยังคุยกับคุณอยู่จนกว่าคุณจะกดโอนเอง ไม่ใช่ตอนที่อีกฝ่ายกดรับ
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
