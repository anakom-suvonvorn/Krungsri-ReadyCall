/**
 * The plan information panel (`D127`).
 *
 * ⚠️ This replaces the comparison block that sat in the right column. That block was the
 * comparison and nothing else, permanently expanded, with a table wide enough to need its
 * own scrollbar inside a 340px column — the feature arrived before the place for it did.
 *
 * The place for it is the thing a broker actually has open: **what does the market
 * offer**. Comparison is one answer to that, and it is not the only one — reading a single
 * plan's figures out loud, or checking what a carrier covers before saying anything, are
 * the same question at a different zoom. So the rail carries a compact panel, the detail
 * lives in a dialog, and the comparison is a tab inside it rather than the whole of it.
 *
 * The rules it inherits, unchanged from `D126`:
 *
 * 1. **The ranking is the server's** — not sorted, scored or re-ordered here.
 * 2. **The figures are the server's** — a coverage number is data read from the record,
 *    never something a browser assembled (`D16`).
 * 3. **What is WORSE renders as prominently as what is better.** A panel showing only the
 *    upside is a sales script, and the plan ranked first is frequently the one with a
 *    deductible attached.
 */

import { useEffect, useState } from "react";
import type { ComparisonView, PlanCatalogue } from "./api";

export function PlansPanel({
  callId,
  comparison,
  onOpen,
}: {
  callId: string | null;
  comparison: ComparisonView | null;
  onOpen: () => void;
}) {
  const top = comparison?.available ? comparison.candidates[0] : null;
  return (
    <section className="panel">
      <div className="queue-head">
        <h2>ข้อมูลแผนประกัน</h2>
      </div>
      {!callId ? (
        <p className="faint">ใช้ได้เมื่อรับสายแล้ว</p>
      ) : (
        <>
          {/* A one-line preview, so the rail says something useful without being opened.
              The full ranking, the table and the push all live in the dialog. */}
          {top ? (
            <p className="faint">
              ตรงที่สุดตอนนี้: <b>{top.name_th}</b> · {top.insurer}
            </p>
          ) : (
            <p className="faint">ดูแผนในตลาด และเปรียบเทียบกับแผนที่ลูกค้าถืออยู่</p>
          )}
          <button onClick={onOpen}>เปิดข้อมูลแผน</button>
        </>
      )}
    </section>
  );
}

export function PlansDialog({
  open,
  comparison,
  catalogue,
  busy,
  paired,
  onClose,
  onPickLine,
  onPushComparison,
  onPushPlan,
}: {
  open: boolean;
  comparison: ComparisonView | null;
  catalogue: PlanCatalogue | null;
  busy: boolean;
  paired: boolean;
  onClose: () => void;
  onPickLine: (line: string) => void;
  onPushComparison: () => void;
  onPushPlan: (productCode: string) => void;
}) {
  const [tab, setTab] = useState<"compare" | "browse">("compare");
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // The selected plan belongs to the line being browsed. Keeping it across a line change
  // would leave a health plan selected while the motor catalogue is on screen.
  useEffect(() => setSelected(null), [catalogue?.line]);

  const plan = catalogue?.plans.find((p) => p.product_code === selected) ?? null;
  const pushHint = paired ? undefined : "ยังไม่มีหน้าจอลูกค้าที่เชื่อมต่อ — ส่งลิงก์ให้ลูกค้าก่อน";

  return (
    <div
      className={open ? "scrim" : "scrim hidden"}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="plans-dialog">
        <div className="queue-head">
          <h2>ข้อมูลแผนประกัน</h2>
          <span className="faint">คลิกนอกกรอบเพื่อปิด</span>
        </div>

        {/* One line selector for both tabs: it is the same question either way, and two
            copies of it would be two things to leave pointing at different lines. */}
        <div className="pill-grid" role="radiogroup" aria-label="ประเภทประกัน">
          {catalogue?.lines.map((l) => (
            <button
              key={l.line}
              type="button"
              role="radio"
              aria-checked={catalogue.line === l.line}
              disabled={busy}
              className={`pill-opt ${catalogue.line === l.line ? "on" : ""}`}
              onClick={() => onPickLine(l.line)}
            >
              {l.label_th}
            </button>
          ))}
        </div>

        <div className="tabs">
          <button className={tab === "compare" ? "on" : ""} onClick={() => setTab("compare")}>
            เปรียบเทียบ
          </button>
          <button className={tab === "browse" ? "on" : ""} onClick={() => setTab("browse")}>
            แผนทั้งหมด{catalogue ? ` (${catalogue.plans.length})` : ""}
          </button>
        </div>

        {tab === "compare" ? (
          <ComparePane
            view={comparison}
            busy={busy}
            paired={paired}
            pushHint={pushHint}
            onPush={onPushComparison}
          />
        ) : (
          <div className="plan-cols">
            <div className="plan-list" role="listbox" aria-label="แผนในตลาด">
              {catalogue?.plans.map((p) => (
                <button
                  key={p.product_code}
                  role="option"
                  aria-selected={selected === p.product_code}
                  className={`plan-row ${selected === p.product_code ? "on" : ""}`}
                  onClick={() => setSelected(p.product_code)}
                >
                  <b>{p.name_th}</b>
                  <span className="faint">{p.insurer}</span>
                  {/* Marked rather than hidden: the plan the customer is ON is the most
                      useful row in the list when the broker is explaining a gap. */}
                  {p.held && <span className="chip ok">แผนปัจจุบันของลูกค้า</span>}
                </button>
              ))}
              {catalogue && !catalogue.plans.length && (
                <p className="faint">ยังไม่มีข้อมูลแผนในหมวดนี้</p>
              )}
            </div>

            <div className="plan-detail">
              {plan === null ? (
                <p className="faint">เลือกแผนทางซ้ายเพื่อดูความคุ้มครอง</p>
              ) : (
                <>
                  <h3>{plan.name_th}</h3>
                  <p className="faint">
                    {plan.insurer}
                    {plan.short_desc ? ` · ${plan.short_desc}` : ""}
                  </p>
                  <dl className="kv">
                    {plan.coverages.map((c) => (
                      <div key={c.kind}>
                        <dt className="faint">{c.label_th}</dt>
                        <dd>{figure(c)}</dd>
                      </div>
                    ))}
                  </dl>
                  <button
                    className="primary"
                    disabled={busy || !paired}
                    title={pushHint}
                    onClick={() => onPushPlan(plan.product_code)}
                  >
                    ส่งรายละเอียดแผนนี้ให้ลูกค้า
                  </button>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** A figure as the customer would hear it. **Never `0` for an absent one** — a plan that
 *  does not state a cover and one that excludes it are different products (`D125`). */
function figure(c: { amount: number | null; unit: string | null }): string {
  if (c.amount === null) return "—";
  if (c.unit === "percent") return `${c.amount.toLocaleString("th-TH")}%`;
  const suffix =
    { per_day: "ต่อวัน", per_year: "ต่อปี", per_visit: "ต่อครั้ง", per_accident: "ต่อครั้ง" }[
      c.unit ?? ""
    ] ?? "";
  return `${c.amount.toLocaleString("th-TH")} บาท${suffix}`;
}

function ComparePane({
  view,
  busy,
  paired,
  pushHint,
  onPush,
}: {
  view: ComparisonView | null;
  busy: boolean;
  paired: boolean;
  pushHint?: string;
  onPush: () => void;
}) {
  if (view === null || !view.available) {
    // Not an error state. A line with no comparable attributes configured, or a catalogue
    // with nothing in it, is an ordinary thing to be told (`D125`).
    return <p className="faint">ยังไม่มีข้อมูลแผนสำหรับเปรียบเทียบในหมวดนี้</p>;
  }
  return (
    <>
      <p className="faint">
        {view.held_policy_no ? (
          <>
            เทียบกับ <b>{view.held_policy_no}</b>
            {view.held_insurer ? ` (${view.held_insurer})` : ""}
          </>
        ) : (
          "ลูกค้ายังไม่มีความคุ้มครองในหมวดนี้ — เรียงตามความคุ้มครองที่ได้"
        )}
      </p>

      <ol className="candidates">
        {view.candidates.map((c) => (
          <li key={c.product_code}>
            <div className="cand-head">
              <b>{c.name_th}</b>
              <span className="faint"> · {c.insurer}</span>
            </div>
            <div className="faint">{c.reason_th}</div>
            <div className="chips">
              {c.better_on.map((label) => (
                <span key={`b-${label}`} className="chip ok">
                  ↑ {label}
                </span>
              ))}
              {/* Deliberately the same size and weight as the green ones (`D126`). */}
              {c.worse_on.map((label) => (
                <span key={`w-${label}`} className="chip bad">
                  ↓ {label}
                </span>
              ))}
            </div>
          </li>
        ))}
      </ol>

      <div className="tw">
        <table className="cmp">
          <thead>
            <tr>
              {view.table.columns_th.map((col) => (
                <th key={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.table.rows.map((row) => (
              <tr key={String(row.cells[0])}>
                {row.cells.map((cell, i) => (
                  <td key={i} className={row.best_index === i ? "best" : undefined}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="faint">{view.table.note_th}</p>

      <button className="primary" disabled={busy || !paired} title={pushHint} onClick={onPush}>
        ส่งตารางนี้ให้ลูกค้า
      </button>
    </>
  );
}
