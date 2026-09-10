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
  onPush: (toolId: string, payload?: Record<string, unknown>) => void;
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

  // Poll once a link exists, OR while the dialog is open — not only while it is open
  // (`B35`).
  //
  // Polling only on `open` deadlocked: the customer tapping the link is an event that
  // happens on THEIR device, so the only way the broker learns of it is a poll — and the
  // button that starts the polling is itself disabled until `paired` is true. The panel
  // therefore sat on "ยังไม่ได้เชื่อมหน้าจอ" forever while the customer stared at an open
  // page, and only a full page reload broke the tie.
  //
  // A workstation with no link minted still polls for nothing, which is the case this
  // condition is careful to exclude.
  const watching = open || Boolean(state?.link);
  useEffect(() => {
    if (!watching || !callId) return;
    const timer = window.setInterval(() => latestRefresh.current(), 2000);
    return () => window.clearInterval(timer);
  }, [watching, callId]);

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
  const [copied, setCopied] = useState(false);

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
          {/* `D147`. A token makes this ~55 characters, which overflowed the rail's card
              and ran off the right edge. It WRAPS now — and the copy button is the real
              fix, because the thing a broker does with this link is send it, and
              selecting wrapped text by hand mid-call is not that. */}
          <div className="link-row">
            <code className="mono link-text">{window.location.origin + link}</code>
            <button
              className="ghost"
              title="คัดลอกลิงก์"
              onClick={() => {
                const full = window.location.origin + link;
                /* ⚠️ `navigator.clipboard` is undefined on a page served over plain HTTP
                   from anything but localhost — which is exactly how this is demoed on a
                   phone over the venue wifi. The textarea fallback is not belt-and-braces,
                   it is the path that will actually run there. */
                const done = () => {
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1500);
                };
                if (navigator.clipboard?.writeText) {
                  void navigator.clipboard.writeText(full).then(done, () => undefined);
                  return;
                }
                const box = document.createElement("textarea");
                box.value = full;
                box.style.position = "fixed";
                box.style.opacity = "0";
                document.body.appendChild(box);
                box.select();
                try {
                  document.execCommand("copy");
                  done();
                } finally {
                  document.body.removeChild(box);
                }
              }}
            >
              {copied ? "คัดลอกแล้ว ✓" : "คัดลอก"}
            </button>
          </div>
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
  onPush: (toolId: string, payload?: Record<string, unknown>) => void;
  onClose: () => void;
}) {
  // Escape closes it, like every other dismissable surface in the browser. Registered
  // only while open, so it cannot swallow Escape from anything else.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // `.scrim.hidden` rather than unmounting, matching the offer card — and the class must
  // stay two-part, because a single-class `.hidden` loses to `.scrim`'s display:flex and
  // leaves an invisible overlay eating every click.
  //
  // Dismissed by clicking the backdrop rather than by a ปิด button: the backdrop is
  // already there, it is a bigger target than any button, and it is what every dialog on
  // a phone does. `e.target === e.currentTarget` is load-bearing — without it a click
  // anywhere INSIDE the dialog bubbles up and closes it, which is the classic version of
  // this bug and it is infuriating mid-form.
  return (
    <div
      className={open ? "scrim" : "scrim hidden"}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="tool-rail">
        <div className="queue-head">
          <h2>กล่องเครื่องมือ</h2>
          <span className="faint">คลิกนอกกรอบเพื่อปิด</span>
        </div>

        {!verified && (
          <p className="faint">
            เครื่องมือที่เป็นข้อมูลส่วนบุคคลจะใช้ไม่ได้ จนกว่าลูกค้าจะเข้าสู่ระบบบนหน้าจอของตัวเอง
          </p>
        )}

        {/* Two columns: what the broker can SEND on the left, what came BACK on the
            right. Stacking them meant the responses sat below four groups of tools, so
            the thing the broker is waiting for was the thing furthest off screen. */}
        <div className="tool-cols">
          <div className="tool-col">
            {catalogue?.groups.map((group) => (
              <div key={group.group_id} className="stack">
                <h3>{group.label_th}</h3>
                {/* A horizontal strip rather than a wrapping grid, so adding a fifth tool
                    to a group makes that ROW scroll instead of making the whole dialog
                    taller. The dialog's height is the thing worth keeping stable. */}
                <div className="tool-strip">
                  {group.tools.map((tool) =>
                    tool.tool_id === "note.agent_message" ? (
                      <AgentMessageTool
                        key={tool.tool_id}
                        tool={tool}
                        busy={busy}
                        verified={verified}
                        onPush={onPush}
                      />
                    ) : (
                      <ToolButton
                        key={tool.tool_id}
                        tool={tool}
                        locked={tool.personal && !verified}
                        busy={busy}
                        onPush={onPush}
                      />
                    ),
                  )}
                </div>
              </div>
            ))}
          </div>

          <div className="tool-col">
            <h3>ลูกค้าตอบกลับ</h3>
            {!state?.items.some((i) => i.responded) && (
              <p className="faint">ยังไม่มีสิ่งที่ลูกค้าส่งกลับมา</p>
            )}
            {state?.items
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
        </div>
      </div>
    </div>
  );
}

/**
 * The one tool the broker writes themselves (`D128`).
 *
 * Every other control on this rail sends something the SYSTEM composed from a record — a
 * comparison, a prefilled form, a checklist. This sends the broker's own sentence: the
 * thing they are already saying out loud, written down so the customer can read a hospital
 * name, a spelling, a reference number or a set of steps instead of trying to catch it
 * over a bad line. It is `form.free_text` (`D122`) pointed the other way.
 *
 * ⚠️ **The tier gate cannot help here, and the banner is the answer.** `personal` says what
 * a tool is FOR (`D121`), and this one is for whatever the broker needs to say — the
 * system cannot classify a sentence it did not write. So the composer puts the screen's
 * current tier in front of them **while they type**, which is the fact they need in order
 * to decide. `D71`'s pattern: give the person the reason, do not fake a refusal the system
 * is not equipped to make.
 */
function AgentMessageTool({
  tool,
  busy,
  verified,
  onPush,
}: {
  tool: AssistTool;
  busy: boolean;
  verified: boolean;
  onPush: (toolId: string, payload?: Record<string, unknown>) => void;
}) {
  const [text, setText] = useState("");
  const send = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onPush(tool.tool_id, { text_th: trimmed });
    setText("");
  };
  return (
    <div className="tool composer">
      <span className="tool-label">{tool.label_th}</span>
      <textarea
        rows={3}
        value={text}
        maxLength={1200}
        placeholder="พิมพ์หรือวางข้อความที่จะให้ลูกค้าอ่าน"
        disabled={busy}
        onChange={(e) => setText(e.target.value)}
        // Ctrl/⌘+Enter sends. A bare Enter must NOT: an address or a set of steps is
        // several lines, and losing the newline is the whole reason to type it here.
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send();
        }}
      />
      <span className={`badge ${verified ? "ok" : "warn"}`}>
        {verified
          ? "ลูกค้าเข้าสู่ระบบแล้ว"
          : "ลูกค้ายังไม่ได้เข้าสู่ระบบ — เลี่ยงข้อมูลส่วนบุคคล"}
      </span>
      <button className="primary" disabled={busy || !text.trim()} onClick={send}>
        ส่งข้อความ
      </button>
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
  onPush: (toolId: string, payload?: Record<string, unknown>) => void;
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
