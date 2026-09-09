/**
 * The ⚙ settings panel: switch the AI model on and off without a restart (`D138`).
 *
 * ⚠️ **Why this exists at all.** Until now "is the AI on?" was decided by `LLM_PROVIDER`
 * in `.env`, read once at startup into a frozen `Settings`. Changing it meant editing a
 * file and restarting the server — which is fine for a deployment and useless in a pitch,
 * where the strongest thing you can show is the *same call* with the model and without it.
 *
 * ⚠️ **It reports as much as it changes.** `B41` was a screen saying "no model configured"
 * on a machine that was calling one three times per call, because `LLM_FAST_MODEL` builds
 * a real client whatever `LLM_PROVIDER` says. So this panel names the two stages
 * separately and takes its answer from the server, never from a guess about config.
 *
 * Nothing here is persisted: a restart returns to whatever `.env` says (`D138`).
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { LlmSettings } from "./api";

/** `D129`: one-of-many is pills, not radio rows. Native radios draw a bullet that never
 *  lines up beside a Thai label and give each option a full-width row. */
function Choice({
  option,
  selected,
  busy,
  onSelect,
}: {
  option: LlmSettings["options"][number];
  selected: boolean;
  busy: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      /* ⚠️ The `disabled` attribute is a COURTESY, not the gate (`D121`, `B5`). The server
         refuses an unavailable provider independently, and the test that matters asserts
         the SERVER's refusal. */
      disabled={busy || !option.available}
      title={option.why_th}
      className={`pill-opt ${selected ? "on" : ""}`}
      onClick={onSelect}
    >
      <b>{option.label_th}</b>
      {option.model && <span className="faint"> · {option.model}</span>}
    </button>
  );
}

export function SettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [llm, setLlm] = useState<LlmSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* `B32`: "fetch once" must mean once AFTER the precondition. Keyed to `open` rather than
     an empty dependency array, so it runs when the dialog is actually shown and not before
     sign-in — where it would 401 into a permanently empty panel. */
  const refresh = useCallback(async () => {
    try {
      setLlm((await api.settings()).llm);
      setError(null);
    } catch {
      setError("อ่านการตั้งค่าไม่สำเร็จ");
    }
  }, []);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  if (!open) return null;

  const choose = async (provider: string) => {
    setBusy(true);
    setError(null);
    try {
      setLlm((await api.setLlm(provider)).llm);
    } catch (e) {
      setError(e instanceof Error ? e.message : "สลับโมเดลไม่สำเร็จ");
      /* The server may have refused for a reason the panel could not see, so re-read
         rather than leaving the pills showing a selection that did not happen. */
      void refresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="scrim"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="transfer-dialog">
        <div className="queue-head">
          <h2>ตั้งค่าระบบ (เดโม)</h2>
          <span className="faint">คลิกนอกกรอบเพื่อปิด</span>
        </div>

        {error && (
          <div className="rationale" style={{ borderLeftColor: "var(--bad)" }}>
            {error}
          </div>
        )}

        <h3>โมเดล AI</h3>
        <p className="faint">
          เปลี่ยนได้ทันทีโดยไม่ต้องรีสตาร์ต — ใช้สำหรับสาธิตว่า “มี AI” กับ “ไม่มี AI”
          ต่างกันอย่างไรบนสายเดียวกัน <b>ไม่ถูกบันทึกไว้</b> รีสตาร์ตแล้วจะกลับไปตามไฟล์{" "}
          <code>.env</code>
        </p>

        {llm === null ? (
          <div className="faint">กำลังอ่าน…</div>
        ) : (
          <>
            <div className="pill-grid" role="radiogroup" aria-label="โมเดล AI">
              {llm.options.map((o) => (
                <Choice
                  key={o.provider}
                  option={o}
                  selected={o.provider === llm.provider}
                  busy={busy}
                  onSelect={() => void choose(o.provider)}
                />
              ))}
            </div>

            {/* ⚠️ The two stages are named APART, which is `B41`'s fix on screen. They can
                genuinely differ — a fast model configured while the main one is rule-based
                means three of the four AI features call a hosted model — and one combined
                sentence is exactly what hid that. */}
            <div className="rationale" style={{ marginTop: 10 }}>
              <div>
                {llm.model ? "✅" : "⬜"} สรุปฉบับเต็ม (หลังกดรับสาย) —{" "}
                {llm.model ?? "ใช้สรุปแบบกฎ ไม่เรียกโมเดล"}
              </div>
              <div style={{ marginTop: 4 }}>
                {llm.fast_model ? "✅" : "⬜"} สรุประหว่างรอรับสาย · แผงข้อมูลลูกค้า ·
                เหตุผลเปรียบเทียบแผน — {llm.fast_model ?? "ใช้สรุปแบบกฎ ไม่เรียกโมเดล"}
              </div>
              {llm.fast_model && !llm.model && (
                <div className="faint" style={{ marginTop: 6 }}>
                  ⚠️ ตั้งค่าเฉพาะโมเดลเร็วไว้ — สามในสี่จุดที่ใช้ AI จะเรียกโมเดลจริง
                  แม้ <code>LLM_PROVIDER</code> จะเป็น <code>rulebased</code> (`B41`)
                </div>
              )}
            </div>

            <p className="faint" style={{ marginTop: 10 }}>
              ตัวเลือกที่กดไม่ได้คือยังไม่มีคีย์ในไฟล์ <code>.env</code> ของเครื่องนี้ —
              เพิ่ม <code>ANTHROPIC_API_KEY</code> หรือ <code>OPENAI_API_KEY</code> แล้วรีสตาร์ต
              เซิร์ฟเวอร์หนึ่งครั้ง คีย์ไม่เคยถูกส่งมาที่หน้าจอนี้
            </p>
          </>
        )}
      </div>
    </div>
  );
}

/** The header button. Its own component so `App.tsx` holds one line rather than state. */
export function SettingsButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        className="ghost"
        title="ตั้งค่าเดโม — เปิด/ปิดโมเดล AI ได้ทันที"
        onClick={() => setOpen(true)}
      >
        ⚙
      </button>
      <SettingsDialog open={open} onClose={() => setOpen(false)} />
    </>
  );
}
