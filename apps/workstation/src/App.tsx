/**
 * The workstation shell (`D32`): one browser tab, and the call happens inside it.
 *
 * The softphone is **stubbed**. Mute and hold move UI state; hangup calls the server. The
 * WebRTC/SIP leg lands at P5, and none of the states or the handshake change when it does.
 *
 * Three rules this file has to hold, each of which it broke once:
 *
 * 1. **The server owns state; this owns presentation.** Every mutation returns a fresh
 *    snapshot. Nothing is recomputed here that the server already decided — not
 *    `offerable`, not what the brief may show (`D53`), not which status buttons are legal
 *    (`D59`).
 * 2. **Timers are drawn from server timestamps**, never from "seconds since mount". That
 *    is why the call timer survives a refresh now.
 * 3. **A background refresh must not disable the UI.** Marking the app `busy` on
 *    socket-driven refreshes made every button grey out and come back once a second —
 *    which is the "flickering" that looked like a React problem and was not.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "./api";
import type { Capture, Snapshot } from "./api";
import { useSocket } from "./useSocket";
import type { SocketMessage } from "./useSocket";
import {
  BriefPanel,
  clockSkewMs,
  CapturePanel,
  IdentityPanel,
  OfferCard,
  PresencePanel,
  QueueStrip,
  WrapupPanel,
  elapsedSince,
  mmss,
  useSecondTicker,
} from "./panels";

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [muted, setMuted] = useState(false);
  const [held, setHeld] = useState(false);
  /** `server - browser`, in ms, sampled once per snapshot. See `B8`. */
  const [skew, setSkew] = useState(0);

  const signedIn = snapshot !== null;
  const now = useSecondTicker(signedIn);

  // --- talking to the server ---------------------------------------------

  const handleFailure = useCallback((exc: unknown) => {
    // A 401 means the session went away (server restart, expiry). Drop to the sign-in
    // screen rather than leaving a dead workstation that silently does nothing.
    if (exc instanceof ApiError && exc.status === 401) {
      setSnapshot(null);
      return;
    }
    setError(exc instanceof Error ? exc.message : String(exc));
  }, []);

  /** A user-initiated action: shows progress, surfaces errors, disables controls. */
  const run = useCallback(
    async <T,>(work: () => Promise<T>): Promise<T | null> => {
      setBusy(true);
      setError(null);
      try {
        return await work();
      } catch (exc) {
        handleFailure(exc);
        return null;
      } finally {
        setBusy(false);
      }
    },
    [handleFailure],
  );

  /**
   * A background re-read. **Deliberately does not touch `busy`.**
   *
   * The socket pushes several messages a second during an active call, and routing each
   * one through `run()` toggled `busy` on and off — greying every control for a frame,
   * over and over. That was the flicker.
   */
  const quietRefresh = useCallback(async () => {
    try {
      setSnapshot(await api.me());
    } catch (exc) {
      handleFailure(exc);
    }
  }, [handleFailure]);

  useEffect(() => {
    // Resume: the cookie outlives a reload, so an agent who refreshes mid-call lands back
    // on the call rather than at a login form (`D32`'s resilience clause).
    void quietRefresh();
  }, [quietRefresh]);

  const onSocketMessage = useCallback(
    (message: SocketMessage) => {
      if (message.type === "capture") {
        // Patch just this capture rather than re-reading everything: keystrokes arrive
        // fast, and a full snapshot per digit is both wasteful and jumpy.
        const incoming = message.payload as unknown as Capture;
        setSnapshot((prior) =>
          prior === null
            ? prior
            : {
                ...prior,
                captures: prior.captures.some((c) => c.capture_id === incoming.capture_id)
                  ? prior.captures.map((c) =>
                      c.capture_id === incoming.capture_id ? incoming : c,
                    )
                  : [...prior.captures, incoming],
              },
        );
        return;
      }
      void quietRefresh();
    },
    [quietRefresh],
  );

  const socketStatus = useSocket(signedIn, onSocketMessage, setSnapshot);

  // Measured when a snapshot ARRIVES, never per render — see the note where it is used.
  useEffect(() => {
    setSkew(clockSkewMs(snapshot?.server_time));
  }, [snapshot?.server_time]);

  useEffect(() => {
    // The softphone is stubbed, so these are local until P5 brings a real media session
    // with its own authoritative mute state. Clearing them off-call keeps the buttons
    // from lying about a call that has ended.
    if (snapshot?.presence.system_state !== "on_call") {
      setMuted(false);
      setHeld(false);
    }
  }, [snapshot?.presence.system_state]);

  if (!signedIn) return <SignIn onDone={setSnapshot} />;

  const { presence, offer, identity, brief, queues, active_call_session_id: callId } = snapshot;
  const wrapupCallId = snapshot.wrapup_call_session_id;
  const openCapture = snapshot.captures.find((c) => c.state === "open") ?? null;
  const latestCapture = openCapture ?? snapshot.captures[snapshot.captures.length - 1] ?? null;
  const onCall = presence.system_state === "on_call";
  const wrapping = presence.system_state === "after_call_work";

  // Drawn from server timestamps AND corrected for a browser clock that disagrees.
  // `skew` is measured ONCE per snapshot (see the effect above) and held. Recomputing it
  // per render freezes every timer: `skew` would be `server - Date.now()` and `now` would
  // be `Date.now()` from the same render, so `now + skew` collapses to `server_time` --
  // a constant that only moves when the next snapshot lands. That is exactly what made
  // the clocks tick once every ten seconds instead of every second (`B8`).
  const callSeconds = elapsedSince(snapshot.call_answered_at, now, skew);
  const acwSeconds = elapsedSince(presence.acw_since, now, skew);
  // The server computes this from `acw_long_after_s`. The client used to OR it with its
  // own hardcoded 45, so tuning the config left the screen warning at the old threshold.
  const longAcw = presence.long_acw;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          Krungsri <span>ReadyCall</span>
        </div>
        <span className="badge">{presence.display_name}</span>
        <span className={`badge ${presence.offerable ? "ok" : "warn"}`}>
          {presence.offerable ? "รับสายได้" : "ยังไม่รับสาย"}
        </span>
        <div className="spacer" />
        <span
          className={`badge ${
            socketStatus === "live" ? "ok" : socketStatus === "connecting" ? "warn" : "bad"
          }`}
        >
          <i className="dot" />
          {socketStatus === "live"
            ? "เชื่อมต่อแล้ว"
            : socketStatus === "connecting"
              ? "กำลังเชื่อมต่อ"
              : "ขาดการเชื่อมต่อ"}
        </span>
        <DemoCallButton
          onPlaced={quietRefresh}
          // A test caller can only be placed when this agent could actually receive one.
          // Pressing it while un-offerable used to place a call that then sat unmatched,
          // which looks exactly like a routing bug.
          disabled={busy || !presence.offerable || offer !== null}
          reason={
            offer !== null
              ? "มีสายที่กำลังเสนออยู่แล้ว"
              : presence.offerable
                ? undefined
                : "กด “พร้อมรับสาย” ก่อน จึงจะรับสายทดสอบได้"
          }
          skills={presence.skills}
        />
        <button
          className="ghost"
          disabled={busy || onCall}
          title={onCall ? "วางสายก่อนออกจากระบบ" : undefined}
          onClick={() => run(() => api.signOut()).then(() => setSnapshot(null))}
        >
          ออกจากระบบ
        </button>
      </header>

      <div className="columns">
        <div className="col">
          <PresencePanel
            presence={presence}
            busy={busy}
            onDeclare={(intent) =>
              run(async () => {
                await api.declare(intent);
                return api.me();
              }).then((next) => next && setSnapshot(next))
            }
          />
          <QueueStrip queues={queues} />
        </div>

        <div className="col">
          <BriefPanel brief={brief} />
          {/* Keyed on the WRAPPING call, not the active one. Saving closes the record, so
              `active_call_session_id` drops to null at that instant — which used to unmount
              this panel and take the "saved" confirmation with it (`D68`). */}
          {wrapping && wrapupCallId && (
            <WrapupPanel
              presence={presence}
              saved={snapshot.wrapup_saved}
              busy={busy}
              onSave={(payload) =>
                run(() => api.saveWrapup(wrapupCallId, payload)).then(
                  (next) => next && setSnapshot(next),
                )
              }
              // Two requests behind one button, deliberately (`D45`): saving closes the
              // call RECORD, declaring ends after-call work, and either may happen alone.
              onSaveAndDeclare={(payload, intent) =>
                run(async () => {
                  if (!snapshot.wrapup_saved) await api.saveWrapup(wrapupCallId, payload);
                  await api.declare(intent);
                  return api.me();
                }).then((next) => next && setSnapshot(next))
              }
            />
          )}
        </div>

        <div className="col">
          <IdentityPanel
            identity={identity}
            challenges={snapshot.challenges}
            callId={callId}
            busy={busy}
            onAttest={(payload) =>
              callId &&
              run(() => api.attest(callId, payload)).then((next) => next && setSnapshot(next))
            }
          />
          <CapturePanel
            capture={latestCapture}
            callId={callId}
            busy={busy}
            onStart={() =>
              callId && run(() => api.startCapture(callId)).then(() => void quietRefresh())
            }
            // Not optimistic. The client used to append the digit locally as well as
            // taking the socket push, and the two compounded — typing 2024000811 showed
            // 20240008111. One writer: the server.
            onKey={(digit) =>
              openCapture && void run(() => api.sendKeys(openCapture.capture_id, digit))
            }
            onBackspace={() =>
              openCapture &&
              run(() => api.backspaceCapture(openCapture.capture_id)).then(
                () => void quietRefresh(),
              )
            }
            onStop={() =>
              openCapture &&
              run(() => api.stopCapture(openCapture.capture_id)).then(() => void quietRefresh())
            }
            onDiscard={() =>
              latestCapture &&
              run(() => api.discardCapture(latestCapture.capture_id)).then(
                () => void quietRefresh(),
              )
            }
            onLookup={(kind) =>
              latestCapture &&
              run(() => api.lookupCapture(latestCapture.capture_id, kind)).then(
                () => void quietRefresh(),
              )
            }
          />
        </div>
      </div>

      {/* The ACW banner lives HERE, not inside the wrap-up form. Saving the form closes
          the call record (`D45`) and used to unmount the only visible ACW timer with it,
          while the agent was still — correctly — in after-call work. */}
      {wrapping && (
        <div className={`acw-bar ${longAcw ? "long" : ""}`}>
          <span className="badge info">งานหลังจบสาย</span>
          <span className="timer">{mmss(acwSeconds ?? 0)}</span>
          <span className="faint">
            นับตั้งแต่วางสาย — จะหยุดเมื่อคุณเลือกสถานะถัดไปเท่านั้น
          </span>
          {longAcw && <span className="badge warn">ใช้เวลานานกว่าปกติ</span>}
          <div className="spacer" />
          {snapshot.wrapup_saved && <span className="badge ok">บันทึกสรุปแล้ว</span>}
        </div>
      )}

      <footer className="callbar">
        <span className="stub">softphone: stubbed until P5</span>
        {onCall && callId ? (
          <>
            <span className="timer">{mmss(callSeconds ?? 0)}</span>
            <button className={muted ? "on" : ""} onClick={() => setMuted((m) => !m)}>
              {muted ? "เปิดไมค์" : "ปิดไมค์"}
            </button>
            <button className={held ? "on" : ""} onClick={() => setHeld((h) => !h)}>
              {held ? "รับสายต่อ" : "พักสาย"}
            </button>
            <div className="spacer" />
            <button
              className="danger"
              disabled={busy}
              onClick={() =>
                run(() => api.endCall(callId)).then((next) => next && setSnapshot(next))
              }
            >
              วางสาย
            </button>
          </>
        ) : (
          <span className="faint">{wrapping ? "" : "ไม่มีสายที่กำลังสนทนา"}</span>
        )}
      </footer>

      <OfferCard
        offer={offer}
        busy={busy}
        onAccept={() =>
          offer &&
          run(() => api.accept(offer.assignment_id)).then((next) => next && setSnapshot(next))
        }
        onDecline={(reason) =>
          offer &&
          run(() => api.decline(offer.assignment_id, reason)).then(
            (next) => next && setSnapshot(next),
          )
        }
      />

      {error && (
        <div className="toast" onClick={() => setError(null)}>
          {error}
        </div>
      )}
    </div>
  );
}

// --- sign-in --------------------------------------------------------------

function SignIn({ onDone }: { onDone: (snapshot: Snapshot) => void }) {
  const [roster, setRoster] = useState<{ agent_id: string; display_name: string; team: string }[]>(
    [],
  );
  const [agentId, setAgentId] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .roster()
      .then((rows) => {
        setRoster(rows);
        setAgentId(rows[0]?.agent_id ?? "");
      })
      .catch((exc) => setError(String(exc)));
  }, []);

  return (
    <div className="signin">
      <div className="panel">
        <div className="brand" style={{ fontSize: 22, marginBottom: 4 }}>
          Krungsri <span>ReadyCall</span>
        </div>
        <p className="faint">
          DEMO: เลือกเจ้าหน้าที่เพื่อเข้าสู่ระบบ — ระบบจริงใช้บัญชีพนักงานของธนาคาร
        </p>
        <div className="stack">
          <select value={agentId} onChange={(e) => setAgentId(e.target.value)}>
            {roster.map((agent) => (
              <option key={agent.agent_id} value={agent.agent_id}>
                {agent.display_name} · {agent.team}
              </option>
            ))}
          </select>
          <button
            className="primary"
            disabled={!agentId}
            onClick={() =>
              api
                .signIn(agentId)
                .then(() => api.me())
                .then(onDone)
                .catch((exc) => setError(String(exc)))
            }
          >
            เข้าสู่ระบบ
          </button>
        </div>
        {error && <div className="toast">{error}</div>}
      </div>
    </div>
  );
}

// --- DEMO: put a caller on the line ---------------------------------------

/** The intent a test call should use, picked so it reaches the agent who pressed the
 *  button. Derived from their own skills rather than hardcoded: a motor agent pressing
 *  "test call" and watching it go to somebody else is a confusing demo. */
function intentForSkills(skills: { skill_code: string }[]): string {
  const held = new Set(skills.map((s) => s.skill_code));
  const preferred: [string, string][] = [
    ["health.ipd", "health.ipd.preauth"],
    ["health.claim", "health.claim.status"],
    ["health.policy", "health.other"],
    ["motor.claim", "motor.claim.accident"],
    ["motor.policy", "motor.other"],
    ["travel.claim", "travel.claim.submit"],
    ["life.policy", "life.other"],
    ["general.escalation", "general.complaint"],
  ];
  for (const [skill, intent] of preferred) if (held.has(skill)) return intent;
  return "general.other";
}

function DemoCallButton({
  onPlaced,
  disabled,
  reason,
  skills,
}: {
  onPlaced: () => void;
  disabled: boolean;
  reason?: string;
  skills: { skill_code: string }[];
}) {
  const [pending, setPending] = useState(false);
  return (
    <button
      className="ghost"
      disabled={disabled || pending}
      title={reason ?? "DEMO: stands in for telephony until P5"}
      onClick={async () => {
        setPending(true);
        try {
          await api.placeCall({
            intent_code: intentForSkills(skills),
            caller_number: "0812345678",
            waited_s: 40,
            // DEMO: rehearsals happen at 2 a.m., when most queues are shut (`D54`).
            ignore_hours: true,
          });
          onPlaced();
        } finally {
          setPending(false);
        }
      }}
    >
      + สายทดสอบ
    </button>
  );
}
