/**
 * The workstation shell (`D32`): one browser tab, and the call happens inside it.
 *
 * The softphone is **stubbed** here. Mute, hold and hangup move UI state and call the
 * server's call-control endpoints where they exist; the WebRTC/SIP leg lands at P5. That
 * split is on purpose — the states, the handshake and the screen are the parts that need
 * designing, and none of them change when real audio arrives underneath.
 *
 * State discipline: the server's snapshot is the truth, and every mutation returns a new
 * one. The socket pushes changes the client did not cause (an offer, a presence change
 * made elsewhere). The client keeps exactly one piece of state the server does not have —
 * the live digits of an open capture — because those are deliberately never stored (`D44`).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";
import type { Capture, Snapshot } from "./api";
import { useSocket } from "./useSocket";
import type { SocketMessage } from "./useSocket";
import {
  BriefPanel,
  CapturePanel,
  IdentityPanel,
  OfferCard,
  PresencePanel,
  QueueStrip,
  WrapupPanel,
  mmss,
} from "./panels";

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [liveDigits, setLiveDigits] = useState("");
  const [muted, setMuted] = useState(false);
  const [held, setHeld] = useState(false);
  const [savedCalls, setSavedCalls] = useState<Set<string>>(new Set());
  const [callStarted, setCallStarted] = useState<number | null>(null);
  const [, forceTick] = useState(0);

  const signedIn = snapshot !== null;

  // --- talking to the server ---------------------------------------------

  const run = useCallback(async <T,>(work: () => Promise<T>): Promise<T | null> => {
    setBusy(true);
    setError(null);
    try {
      return await work();
    } catch (exc) {
      // A 401 means the session went away (server restart, expiry). Drop to the sign-in
      // screen rather than leaving a dead workstation that silently does nothing.
      if (exc instanceof ApiError && exc.status === 401) {
        setSnapshot(null);
        return null;
      }
      setError(exc instanceof Error ? exc.message : String(exc));
      return null;
    } finally {
      setBusy(false);
    }
  }, []);

  const refresh = useCallback(async () => {
    const next = await run(() => api.me());
    if (next) setSnapshot(next);
  }, [run]);

  useEffect(() => {
    // Try to resume: the cookie outlives a reload, so an agent who refreshes mid-call
    // lands back on the call rather than at a login form (`D32`'s resilience clause).
    api
      .me()
      .then(setSnapshot)
      .catch(() => setSnapshot(null));
  }, []);

  const onSocketMessage = useCallback(
    (message: SocketMessage) => {
      if (message.type === "capture") {
        const payload = message.payload as unknown as Capture;
        // The only thing the client knows that the server does not keep: the digits.
        if (typeof payload.digits === "string") setLiveDigits(payload.digits);
        return;
      }
      // Everything else changes something the snapshot describes, and the snapshot is
      // cheap. Re-fetching rather than patching keeps one source of truth.
      void refresh();
    },
    [refresh],
  );

  const socketStatus = useSocket(signedIn, onSocketMessage, setSnapshot);

  // A once-a-second re-render so the ACW and call timers move. Values come from the
  // server; this only redraws them.
  useEffect(() => {
    const id = window.setInterval(() => forceTick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, []);

  const previousState = useRef<string | null>(null);
  useEffect(() => {
    const state = snapshot?.presence.system_state ?? null;
    if (state === "on_call" && previousState.current !== "on_call") setCallStarted(Date.now());
    if (state !== "on_call") {
      setMuted(false);
      setHeld(false);
    }
    previousState.current = state;
  }, [snapshot?.presence.system_state]);

  // --- sign-in -----------------------------------------------------------

  if (!signedIn) return <SignIn onDone={setSnapshot} />;

  const { presence, offer, identity, brief, queues, active_call_session_id: callId } = snapshot;
  const openCapture = snapshot.captures.find((c) => c.state === "open") ?? null;
  const latestCapture = openCapture ?? snapshot.captures[snapshot.captures.length - 1] ?? null;
  const onCall = presence.system_state === "on_call";
  const wrapping = presence.system_state === "after_call_work";

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          Krungsri <span>ReadyCall</span>
        </div>
        <span className="badge">{presence.display_name}</span>
        <span className={`badge ${presence.offerable ? "ok" : "warn"}`}>
          {presence.system_state} · {presence.agent_intent}
        </span>
        <div className="spacer" />
        <span
          className={`badge ${
            socketStatus === "live" ? "ok" : socketStatus === "connecting" ? "warn" : "bad"
          }`}
        >
          <i className="dot" />
          {socketStatus === "live" ? "เชื่อมต่อแล้ว" : socketStatus === "connecting" ? "กำลังเชื่อมต่อ" : "ขาดการเชื่อมต่อ"}
        </span>
        <DemoCallButton onPlaced={refresh} disabled={busy} skills={presence.skills} />
        <button
          className="ghost"
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
              run(() => api.declare(intent)).then((updated) => {
                if (updated) void refresh();
              })
            }
          />
          <QueueStrip queues={queues} />
        </div>

        <div className="col">
          <BriefPanel brief={brief} />
          {wrapping && callId && (
            <WrapupPanel
              callId={callId}
              acwSeconds={presence.acw_seconds}
              longAcw={presence.long_acw}
              saved={savedCalls.has(callId)}
              busy={busy}
              onSave={(payload) =>
                run(() => api.saveWrapup(callId, payload)).then((next) => {
                  if (next) {
                    setSnapshot(next);
                    setSavedCalls((prior) => new Set(prior).add(callId));
                  }
                })
              }
              onSaveAndReady={(payload) =>
                run(async () => {
                  if (!savedCalls.has(callId)) await api.saveWrapup(callId, payload);
                  await api.declare("ready");
                  return api.me();
                }).then((next) => {
                  if (next) {
                    setSnapshot(next);
                    setSavedCalls((prior) => new Set(prior).add(callId));
                  }
                })
              }
            />
          )}
        </div>

        <div className="col">
          <IdentityPanel
            identity={identity}
            callId={callId}
            busy={busy}
            onAttest={(payload) =>
              callId &&
              run(() => api.attest(callId, payload)).then((next) => next && setSnapshot(next))
            }
          />
          <CapturePanel
            capture={latestCapture}
            liveDigits={liveDigits}
            callId={callId}
            busy={busy}
            onStart={() =>
              callId &&
              run(() => api.startCapture(callId)).then(() => {
                setLiveDigits("");
                void refresh();
              })
            }
            // NOT optimistic. The first version appended the digit locally as well as
            // taking the socket's push, and the two compounded: typing 2024000811
            // showed 20240008111 while the server held ten digits. An agent reads these
            // off the screen and acts on them, so a digit that is not there is far worse
            // than a hundred milliseconds of latency. One writer: the server.
            onKey={(digit) =>
              openCapture && void run(() => api.sendKeys(openCapture.capture_id, digit))
            }
            onStop={() =>
              openCapture &&
              run(() => api.stopCapture(openCapture.capture_id)).then(() => void refresh())
            }
            onDiscard={() =>
              openCapture &&
              run(() => api.discardCapture(openCapture.capture_id)).then(() => {
                setLiveDigits("");
                void refresh();
              })
            }
            onLookup={(kind) =>
              latestCapture &&
              run(() => api.lookupCapture(latestCapture.capture_id, kind)).then(
                () => void refresh(),
              )
            }
          />
        </div>
      </div>

      <footer className="callbar">
        <span className="stub">softphone: stubbed until P5</span>
        {onCall && callId ? (
          <>
            <span className="timer">
              {mmss(callStarted ? (Date.now() - callStarted) / 1000 : 0)}
            </span>
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
          <span className="faint">
            {wrapping ? "กำลังสรุปหลังจบสาย" : "ไม่มีสายที่กำลังสนทนา"}
          </span>
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
  skills,
}: {
  onPlaced: () => void;
  disabled: boolean;
  skills: { skill_code: string }[];
}) {
  const [pending, setPending] = useState(false);
  return (
    <button
      className="ghost"
      disabled={disabled || pending}
      title="DEMO: stands in for telephony until P5"
      onClick={async () => {
        setPending(true);
        try {
          await api.placeCall({
            intent_code: intentForSkills(skills),
            caller_number: "0812345678",
            waited_s: 40,
            // DEMO: rehearsals happen at 2 a.m., when most queues are shut. Marked here
            // and in the request schema; production has no such flag.
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
