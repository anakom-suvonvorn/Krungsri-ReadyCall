/**
 * The tool rail: the broker's half of the customer's paired screen (`D120`, `D121`).
 *
 * This exists because the rail shipped on 2026-09-07 with three working endpoints, a
 * customer page, a passing suite — and **no way for a broker to reach any of it**. It was
 * `curl` or nothing, which is `B24`'s family in a new place: built, correct, tested, and
 * reachable by no user. A feature the person it was built for cannot press is not done.
 *
 * Two rules inherited from `panels.tsx`, and one of its own:
 *
 * 1. **The catalogue is served, never built here** (`D72`). Groups, labels and the
 *    `personal` flag all come from `assist_tools.yaml`. A client that renders its own
 *    list will eventually offer a tool the server refuses, and the client is the copy
 *    that is wrong.
 * 2. **A control that cannot be pressed looks disabled**, with the reason attached — a
 *    silent refusal reads as a broken button.
 * 3. **Greying a personal tool is a courtesy, not the gate.** The server refuses it
 *    independently, and the test that matters asserts the server's refusal rather than
 *    this file's `disabled` attribute.
 *
 * The panel is one button on the right rail rather than a growing list of tools, because
 * the rail is where the keypad already lives and every tool added would push it further
 * down. The tools live in a dialog, grouped.
 */

import { useEffect, useRef, useState } from "react";
import type { AssistCatalogue, AssistState, AssistTool } from "./api";

/** How the link would reach the customer. Nothing is sent yet — `NotifierPort` is P5 —
 *  so this records the choice and hands the link back for the broker to read out, and
 *  the UI says exactly that rather than implying a message went anywhere.
 *
 *  LINE is deliberately unavailable rather than hidden. Its Messaging API cannot push to
 *  a phone number: it needs a user id, which exists only after the customer has added the
 *  official account or signed in through LINE Login. Showing it greyed with that reason
 *  is more honest than a channel picker that silently only ever does one thing. */
const CHANNELS = [
  { id: "sms", label_th: "SMS", available: true, why: null },
  {
    id: "line",
    label_th: "LINE",
    available: false,
    why: "ต้องผูกบัญชี LINE กับลูกค้าก่อน จึงจะส่งได้",
  },
] as const;

export function AssistPanel({
  callId,
  state,
  catalogue,
  busy,
  onMintLink,
  onPush,
  onRefresh,
}: {
  callId: string | null;
  state: AssistState | null;
  catalogue: AssistCatalogue | null;
  busy: boolean;
  onMintLink: () => void;
  onPush: (toolId: string) => void;
  onRefresh: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [channel, setChannel] = useState<string>("sms");
  const [sent, setSent] = useState(false);

  // The callback is held in a ref and the interval reads the ref, so the effect depends
  // on `open` and `callId` ALONE.
  //
  // Naming `onRefresh` in the dependency list is the obvious version and it does not
  // work: the parent passes an inline arrow, so its identity changes on every render,
  // and the workstation re-renders once a second to drive its call timers. The effect
  // therefore tore the interval down and rebuilt it every second — and a 2000 ms interval
  // that is destroyed at 1000 ms never fires at all. It looked exactly like a server that
  // was not returning the customer's answer.
  const latestRefresh = useRef(onRefresh);
  latestRefresh.current = onRefresh;

  // Poll only while the dialog is open. The broker needs to see a form come back, but a
  // background poll on every workstation for a rail nobody has opened is load for nothing.
  useEffect(() => {
    if (!open || !callId) return;
    const timer = window.setInterval(() => latestRefresh.current(), 2000);
    return () => window.clearInterval(timer);
  }, [open, callId]);

  if (!callId) {
    return (
      <div className="panel">
        <h2>เครื่องมือช่วยลูกค้า</h2>
        <p className="faint">ใช้ได้เมื่อรับสายแล้ว</p>
      </div>
    );
  }

  const tier = state?.tier ?? null;
  const verified = tier === "verified";
  const paired = state?.paired ?? false;
  const link = state?.link;

  return (
    <div className="panel">
      <h2>เครื่องมือช่วยลูกค้า</h2>

      <div className="row">
        <span className={`badge ${paired ? (verified ? "ok" : "info") : "warn"}`}>
          {paired ? (verified ? "เข้าสู่ระบบแล้ว" : "เปิดลิงก์แล้ว") : "ยังไม่ได้เชื่อมหน้าจอ"}
        </span>
      </div>

      <p className="faint">
        {verified
          ? "ส่งข้อมูลส่วนบุคคล เช่น แบบฟอร์มที่กรอกไว้ให้ ได้แล้ว"
          : paired
            ? "ส่งข้อมูลทั่วไปได้ ถ้าจะส่งข้อมูลส่วนบุคคล ให้ลูกค้าเข้าสู่ระบบก่อน"
            : "สร้างลิงก์แล้วให้ลูกค้าเปิด เพื่อส่งข้อมูลขึ้นหน้าจอของลูกค้า"}
      </p>

      {!link && (
        <button className="primary" disabled={busy} onClick={onMintLink}>
          สร้างลิงก์ให้ลูกค้า
        </button>
      )}

      {link && (
        <div className="stack">
          <div className="row">
            {CHANNELS.map((c) => (
              <button
                key={c.id}
                className={channel === c.id ? "on" : "ghost"}
                disabled={!c.available}
                title={c.why ?? undefined}
                onClick={() => setChannel(c.id)}
              >
                {c.label_th}
              </button>
            ))}
            <button className="ghost" disabled={busy} onClick={() => setSent(true)}>
              ส่ง
            </button>
          </div>

          {/* An honest stub, labelled (`D115`). Nothing leaves the building: the broker
              reads the link out, which is also exactly what a rehearsal needs. */}
          {sent && (
            <p className="faint">
              ยังไม่ได้ส่งจริง ({channel.toUpperCase()}) — ระบบส่งข้อความยังไม่เชื่อมต่อ
              อ่านลิงก์ให้ลูกค้าฟังได้เลย
            </p>
          )}
          <code className="mono">{window.location.origin + link}</code>
        </div>
      )}

      <div className="row">
        <button disabled={busy || !paired} onClick={() => setOpen(true)}>
          เปิดกล่องเครื่องมือ
        </button>
      </div>
      {!paired && link && (
        <p className="faint">รอลูกค้าเปิดลิงก์ก่อน จึงจะส่งอะไรขึ้นหน้าจอได้</p>
      )}

      {state && state.items.length > 0 && (
        <div className="stack">
          <h3 className="faint">ส่งไปแล้ว</h3>
          {state.items.map((item) => (
            <div key={item.item_id} className="row">
              <span className={`badge ${item.responded ? "ok" : "info"}`}>
                {item.responded ? "ลูกค้าตอบกลับแล้ว" : "รออยู่"}
              </span>
              <span>{item.title_th}</span>
            </div>
          ))}
        </div>
      )}

      <ToolRailDialog
        open={open}
        catalogue={catalogue}
        state={state}
        verified={verified}
        busy={busy}
        onPush={onPush}
        onClose={() => setOpen(false)}
      />
    </div>
  );
}

function ToolRailDialog({
  open,
  catalogue,
  state,
  verified,
  busy,
  onPush,
  onClose,
}: {
  open: boolean;
  catalogue: AssistCatalogue | null;
  state: AssistState | null;
  verified: boolean;
  busy: boolean;
  onPush: (toolId: string) => void;
  onClose: () => void;
}) {
  // `.scrim.hidden` rather than unmounting, matching the offer card — and the class must
  // stay two-part, because a single-class `.hidden` loses to `.scrim`'s display:flex and
  // leaves an invisible overlay eating every click.
  return (
    <div className={open ? "scrim" : "scrim hidden"}>
      <div className="tool-rail">
        <div className="queue-head">
          <h2>กล่องเครื่องมือ</h2>
          <button className="ghost" onClick={onClose}>
            ปิด
          </button>
        </div>

        {!verified && (
          <p className="faint">
            เครื่องมือที่เป็นข้อมูลส่วนบุคคลจะใช้ไม่ได้ จนกว่าลูกค้าจะเข้าสู่ระบบบนหน้าจอของตัวเอง
          </p>
        )}

        {catalogue?.groups.map((group) => (
          <div key={group.group_id} className="stack">
            <h3>{group.label_th}</h3>
            <div className="tool-grid">
              {group.tools.map((tool) => (
                <ToolButton
                  key={tool.tool_id}
                  tool={tool}
                  locked={tool.personal && !verified}
                  busy={busy}
                  onPush={onPush}
                />
              ))}
            </div>
          </div>
        ))}

        {state && state.items.some((i) => i.responded) && (
          <div className="stack">
            <h3>ลูกค้าตอบกลับ</h3>
            {state.items
              .filter((i) => i.responded)
              .map((item) => (
                <div key={item.item_id} className="panel">
                  <strong>{item.title_th}</strong>
                  {/* What the customer typed, as they typed it. The broker copies this
                      into the wrap-up — nothing stores it yet (`Q34`). */}
                  <dl className="kv">
                    {Object.entries(item.response ?? {}).map(([key, value]) => (
                      <div key={key}>
                        <dt className="faint">{key}</dt>
                        <dd>{String(value)}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ToolButton({
  tool,
  locked,
  busy,
  onPush,
}: {
  tool: AssistTool;
  locked: boolean;
  busy: boolean;
  onPush: (toolId: string) => void;
}) {
  return (
    <button
      className="tool"
      disabled={busy || locked}
      title={
        locked
          ? "ข้อมูลส่วนบุคคล — ให้ลูกค้าเข้าสู่ระบบก่อน"
          : (tool.hint_th ?? undefined)
      }
      onClick={() => onPush(tool.tool_id)}
    >
      <span className="tool-label">{tool.label_th}</span>
      <span className="tool-tags">
        {tool.personal && <span className="badge warn">ส่วนบุคคล</span>}
        {tool.stub && <span className="badge info">สาธิต</span>}
      </span>
      {tool.hint_th && <span className="faint">{tool.hint_th}</span>}
    </button>
  );
}
