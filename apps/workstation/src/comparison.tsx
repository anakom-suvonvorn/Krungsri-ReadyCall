/**
 * Compare & best-fit on the broker's screen (`D126`).
 *
 * The broker's actual mandate — *"คัดสรรแบบประกันและบริษัทฯ ที่ตรงตามความต้องการ"*,
 * select the plan **and the company** (`D117`) — and the brief marks journey step 3 as
 * the **LEAK สูงสุด**.
 *
 * Three rules this file obeys, and all three are about who decides what:
 *
 * 1. **The ranking is the server's.** Not sorted, not scored, not re-ordered here. The
 *    order is a decision made from real figures with weights from
 *    `config/comparison.yaml`, and a client that ranked would eventually disagree with
 *    the reason sentence printed beside it.
 * 2. **The figures are the server's too.** This renders `table` exactly as it arrives —
 *    a coverage number is data read from the record, never something a browser
 *    assembled (`D16`).
 * 3. **What is WORSE renders as prominently as what is better.** A panel that shows only
 *    the upside is a sales script, and the plan ranked first is frequently the one with a
 *    deductible attached.
 *
 * The broker sees the same table the customer will, before pushing it — so *send* is
 * confirming something they have read rather than triggering something they have not.
 */

import type { ComparisonView } from "./api";

export function ComparisonPanel({
  view,
  busy,
  paired,
  onRefresh,
  onPush,
}: {
  view: ComparisonView | null;
  busy: boolean;
  paired: boolean;
  onRefresh: () => void;
  onPush: () => void;
}) {
  return (
    <section className="panel">
      <div className="queue-head">
        <h2>เปรียบเทียบแผน</h2>
        <button className="ghost small" disabled={busy} onClick={onRefresh}>
          โหลดใหม่
        </button>
      </div>

      {view === null && <p className="faint">ใช้ได้เมื่อรับสายแล้ว</p>}

      {/* Not an error state. A line with no comparable attributes configured, or a
          catalogue with nothing in it, is an ordinary thing to be told (`D125`). */}
      {view !== null && !view.available && (
        <p className="faint">ยังไม่มีข้อมูลแผนสำหรับเปรียบเทียบในสายนี้</p>
      )}

      {view?.available && (
        <>
          <p className="faint">
            {view.line_label_th}
            {view.held_policy_no ? (
              <>
                {" · เทียบกับ "}
                <b>{view.held_policy_no}</b>
                {view.held_insurer ? ` (${view.held_insurer})` : ""}
              </>
            ) : (
              " · ลูกค้ายังไม่มีความคุ้มครองในหมวดนี้"
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
                  {/* Deliberately the same size and weight as the green ones. */}
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

          {/* Disabled with the reason attached rather than failing on click (`D71`).
              Pushing needs a paired screen, which is a fact the server already knows. */}
          <button
            className="primary"
            disabled={busy || !paired}
            title={paired ? undefined : "ยังไม่มีหน้าจอลูกค้าที่เชื่อมต่อ — ส่งลิงก์ให้ลูกค้าก่อน"}
            onClick={onPush}
          >
            ส่งตารางนี้ให้ลูกค้า
          </button>
        </>
      )}
    </section>
  );
}
