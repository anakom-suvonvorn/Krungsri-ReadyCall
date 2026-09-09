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
import type {
  AssistCatalogue,
  AssistState,
  ComparisonView,
  HandoffOptions,
  PlanCatalogue,
} from "./api";
import type { Capture, Snapshot, TranscriptTurn } from "./api";
import { useSocket } from "./useSocket";
import type { SocketMessage } from "./useSocket";
import { AssistPanel } from "./assist";
import { TestCallDialog } from "./testcall";
import { TransferButton, TransferDialog } from "./transfer";
import { PlansDialog, PlansPanel } from "./plans";
import {
  BacklogPanel,
  BriefPanel,
  clockSkewMs,
  CapturePanel,
  IdentityPanel,
  OfferCard,
  PresencePanel,
  QueueStrip,
  TranscriptPanel,
  WrapupPanel,
  elapsedSince,
  mmss,
  useSecondTicker,
} from "./panels";

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** A short confirmation for actions whose only other feedback is something *leaving*
   *  the screen. Saving a wrap-up closes a record the agent cannot see afterwards, so
   *  without this the successful case and the silently-failed one look identical. */
  const [toast, setToast] = useState<string | null>(null);
  /** A backlog wrap-up the agent has opened to file late (`D87`). Null means they are
   *  either wrapping up the current call or doing nothing. */
  const [filing, setFiling] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const [held, setHeld] = useState(false);
  /** `server - browser`, in ms, sampled once per snapshot. See `B8`. */
  const [skew, setSkew] = useState(0);
  // The tool rail's own state (`D121`). Kept beside the snapshot rather than inside it:
  // the rail is a second, slower conversation about the customer's screen, and folding it
  // into `/me` would put it on the hot path that every socket push already walks.
  const [assist, setAssist] = useState<AssistState | null>(null);
  const [tools, setTools] = useState<AssistCatalogue | null>(null);
  const [transferOpen, setTransferOpen] = useState(false);
  const [handoffOptions, setHandoffOptions] = useState<HandoffOptions | null>(null);
  const [comparison, setComparison] = useState<ComparisonView | null>(null);
  const [catalogue, setCatalogue] = useState<PlanCatalogue | null>(null);
  const [plansOpen, setPlansOpen] = useState(false);
  /** Which line the dialog is looking at. `null` means "whatever this call is about",
   *  which is the server's own default — the client does not guess it. */
  const [line, setLine] = useState<string | null>(null);

  useEffect(() => {
    if (toast === null) return;
    const id = window.setTimeout(() => setToast(null), 3200);
    return () => window.clearTimeout(id);
  }, [toast]);

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

  /** Re-read the customer's screen. Safe to call often; it is a small payload and it is
   *  the only way the broker learns a form came back. */
  const refreshAssist = useCallback(async () => {
    const id = snapshot?.active_call_session_id ?? null;
    if (!id) {
      setAssist(null);
      return;
    }
    try {
      setAssist(await api.assistState(id));
    } catch {
      // A rail that cannot be read must never break the call screen (`D12`'s shape): the
      // panel simply shows its "not connected" state.
      setAssist(null);
    }
  }, [snapshot?.active_call_session_id]);

  useEffect(() => {
    void refreshAssist();
  }, [refreshAssist]);

  /** Fetched when the dialog OPENS, not on every call. The options depend on the call —
   *  the carrier on this customer's policy, and whether we hold one at all — so they
   *  cannot be fetched once per sign-in the way the tool catalogue is; and fetching them
   *  on every call would be a round trip per call for a dialog nobody may open. */
  const activeCallId = snapshot?.active_call_session_id ?? null;

  /** Re-read the comparison. Keyed to the CALL rather than fetched once per sign-in: the
   *  ranking depends on which policy this caller holds, so it is a different answer for
   *  every call and a stale one is a table about somebody else. */
  const refreshComparison = useCallback(async () => {
    if (!activeCallId) {
      setComparison(null);
      setCatalogue(null);
      return;
    }
    try {
      setComparison(await api.comparison(activeCallId, line ?? undefined));
    } catch {
      // A panel that cannot load must never break the call screen (`D12`'s shape).
      setComparison(null);
    }
    try {
      setCatalogue(await api.plans(activeCallId, line ?? undefined));
    } catch {
      setCatalogue(null);
    }
  }, [activeCallId, line]);

  useEffect(() => {
    void refreshComparison();
  }, [refreshComparison]);

  // A new call is a different customer, so the line the LAST call was about must not
  // carry over — a motor catalogue on a health call is a table about nobody.
  useEffect(() => setLine(null), [activeCallId]);

  useEffect(() => {
    if (!transferOpen || !activeCallId) return;
    void api
      .handoffOptions(activeCallId)
      .then(setHandoffOptions)
      .catch(() => setHandoffOptions(null));
  }, [transferOpen, activeCallId]);

  const agentId = snapshot?.presence.agent_id ?? null;
  useEffect(() => {
    // Fetched once per SIGN-IN, not once per mount. On mount there is no agent cookie
    // yet, so the request 401s, the catch swallows it, and the rail renders empty for
    // the whole shift — a one-shot fetch whose precondition had not happened yet.
    // The catalogue itself is static, so keying it to the agent is enough.
    if (!agentId) return;
    void api
      .assistTools()
      .then(setTools)
      .catch(() => setTools(null));
  }, [agentId]);

  const onSocketMessage = useCallback(
    (message: SocketMessage) => {
      if (message.type === "transcript") {
        // Patched in place, like `capture` and for the same reason: turns arrive while the
        // agent is reading, and a full `GET /me` per utterance is both wasteful and jumpy.
        //
        // **Replaced, never appended.** The server sends the complete transcript every
        // time (`D106`), so there is nothing to merge — and merging is exactly how a
        // client ends up showing a transcript with a sentence missing from the middle
        // after one dropped message. The payload also names its call, so a push that
        // arrives as the agent moves between calls cannot paint the wrong one.
        const incoming = message.payload as unknown as {
          call_session_id: string;
          turns: TranscriptTurn[];
        };
        setSnapshot((prior) => {
          if (prior === null) return prior;
          const current = prior.active_call_session_id ?? prior.wrapup_call_session_id;
          if (current !== null && current !== incoming.call_session_id) return prior;
          return { ...prior, transcript: incoming.turns };
        });
        return;
      }
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
          <QueueStrip queues={queues} skewMs={skew} />
        </div>

        <div className="col">
          <BriefPanel brief={brief} />
          {/* Directly under the brief, because it is the evidence for it: the summary
              above is derived from the sentences below, and an agent who doubts a line in
              the brief should not have to go looking for what the caller actually said. */}
          <TranscriptPanel
            turns={snapshot.transcript}
            hasCall={callId !== null || snapshot.wrapup_call_session_id !== null}
            degraded={brief?.degraded}
          />
          {/* Keyed on the WRAPPING call, not the active one. Saving closes the record, so
              `active_call_session_id` drops to null at that instant — which used to unmount
              this panel and take the "saved" confirmation with it (`D68`). */}
          <BacklogPanel
            pending={snapshot.pending_wrapups}
            busy={busy}
            onPick={setFiling}
          />
          {filing && (
            <WrapupPanel
              presence={presence}
              saved={false}
              busy={busy}
              lateFor={
                snapshot.pending_wrapups.find((row) => row.call_session_id === filing) ?? null
              }
              onCancel={() => setFiling(null)}
              onSave={(payload) =>
                run(() => api.saveWrapup(filing, payload)).then((next) => {
                  if (!next) return;
                  setSnapshot(next);
                  setFiling(null);
                  setToast("บันทึกสรุปย้อนหลังเรียบร้อยแล้ว");
                })
              }
              // Filing from the backlog never touches presence: the agent may well be on
              // another call while they do it, and `D45` keeps the two separate anyway.
              onSaveAndDeclare={() => undefined}
            />
          )}
          {!filing && wrapping && wrapupCallId && (
            <WrapupPanel
              presence={presence}
              saved={snapshot.wrapup_saved}
              busy={busy}
              handoff={snapshot.handoff}
              onSave={(payload) =>
                run(() => api.saveWrapup(wrapupCallId, payload)).then((next) => {
                  if (!next) return;
                  setSnapshot(next);
                  setToast("บันทึกสรุปเรียบร้อยแล้ว");
                })
              }
              // Two requests behind one button, deliberately (`D45`): saving closes the
              // call RECORD, declaring ends after-call work, and either may happen alone.
              onSaveAndDeclare={(payload, intent) =>
                run(async () => {
                  if (!snapshot.wrapup_saved) await api.saveWrapup(wrapupCallId, payload);
                  await api.declare(intent);
                  return api.me();
                }).then((next) => {
                  if (!next) return;
                  setSnapshot(next);
                  setToast("บันทึกสรุปเรียบร้อยแล้ว");
                })
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
          {/* The broker's mandate, above the tool rail because it is the thing they are
              on the call to do. Compact here and detailed in a dialog (`D127`): the
              comparison table needs more width than a 340px column has, and it is one
              answer to "what does the market offer" rather than the whole question. */}
          <PlansPanel
            callId={callId}
            comparison={comparison}
            onOpen={() => setPlansOpen(true)}
          />

          {/* Under the keypad, because it is the same idea one step further out: a
              control the broker triggers that changes what the customer's device is
              doing, with the result landing back here (`D44` -> `D120`). */}
          <AssistPanel
            callId={callId}
            state={assist}
            catalogue={tools}
            busy={busy}
            onMintLink={() =>
              callId && run(() => api.assistLink(callId)).then(() => void refreshAssist())
            }
            onPush={(toolId, payload) =>
              callId &&
              run(() => api.assistPush(callId, toolId, payload ?? {})).then(() =>
                void refreshAssist(),
              )
            }
            onRefresh={() => void refreshAssist()}
          />
        </div>
      </div>

      {toast && (
        <div className="toast ok" role="status" aria-live="polite">
          <span className="tick" aria-hidden="true">
            ✓
          </span>
          {toast}
        </div>
      )}

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
            {/* Beside วางสาย because it is the same kind of act — this call is ending —
                and because `D117`'s banner tells the broker so before they speak. Two
                ways out of a call, one of which records where it went (`D124`). */}
            <TransferButton disabled={busy} onOpen={() => setTransferOpen(true)} />
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

      <PlansDialog
        open={plansOpen && Boolean(callId)}
        comparison={comparison}
        catalogue={catalogue}
        busy={busy}
        paired={Boolean(assist?.paired)}
        onClose={() => setPlansOpen(false)}
        onPickLine={setLine}
        onPushComparison={() =>
          callId &&
          run(() => api.assistPush(callId, "compare.plans", { line: line ?? undefined })).then(
            () => {
              void refreshAssist();
              setToast("ส่งตารางเปรียบเทียบไปที่หน้าจอลูกค้าแล้ว");
            },
          )
        }
        onPushPlan={(productCode) =>
          callId &&
          run(() =>
            api.assistPush(callId, "info.plan_detail", { product_code: productCode }),
          ).then(() => {
            void refreshAssist();
            setToast("ส่งรายละเอียดแผนไปที่หน้าจอลูกค้าแล้ว");
          })
        }
      />

      <TransferDialog
        open={transferOpen && Boolean(callId)}
        options={handoffOptions}
        busy={busy}
        onClose={() => setTransferOpen(false)}
        onHandOff={(body) => {
          if (!callId) return;
          void run(() => api.handOff(callId, body)).then((next) => {
            if (!next) return;
            setSnapshot(next);
            setTransferOpen(false);
            // The wrap-up form is already on screen by now, carrying the prefill the
            // server composed. Saying so is the acknowledgement `B11` asked for: the
            // only other feedback is a dialog disappearing, which is indistinguishable
            // from a request that silently failed.
            setToast("ส่งต่อแล้ว — สรุปถูกกรอกไว้ให้ในงานหลังจบสาย");
          });
        }}
      />

      <OfferCard
        offer={offer}
        busy={busy}
        skewMs={skew}
        onAccept={() =>
          offer &&
          run(() => api.accept(offer.assignment_id)).then((next) => next && setSnapshot(next))
        }
        onDecline={(reason, stopOffering) =>
          offer &&
          run(() => api.decline(offer.assignment_id, reason, stopOffering)).then(
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
    ["claims.assist", "health.claim.notify"],
    ["claims.assist", "health.claim.notify"],
    ["health.service", "health.other"],
    ["claims.assist", "motor.claim.notify"],
    ["motor.service", "motor.other"],
    ["claims.assist", "travel.claim.notify"],
    ["life.service", "life.other"],
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
  const [dialog, setDialog] = useState(false);
  const intent = intentForSkills(skills);
  return (
    <>
      <button
        className="ghost"
        disabled={disabled || pending}
        title={reason ?? "DEMO: stands in for telephony until P5"}
        onClick={async () => {
          setPending(true);
          try {
            await api.placeCall({
              intent_code: intent,
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
      {/* `D132`. The plain button above stays exactly as it was, because a one-click
          caller is what you want most of the time. This one opens on its defaults, so
          placing without touching anything gives the same call — what it adds is the
          ability to turn the transcript and the AI summary on where they could
          previously only be reached from a `curl` line in the README. */}
      {/* ⚠️ Deliberately NOT disabled when the agent is un-offerable, unlike the plain
          button above. That guard exists because a one-click call placed while nobody can
          take it sits unmatched and looks like a routing bug — but placing FIRST and going
          ready afterwards is the sequence that lets you watch the transcript arrive and
          the AI preview land on the card, which is the whole reason this dialog exists.
          The dialog says what will happen instead of refusing (`D71`). */}
      <button
        className="ghost"
        disabled={pending}
        title="DEMO: เลือกได้ว่าจะทดสอบถอดเสียง/ให้โมเดลสรุปด้วยหรือไม่"
        onClick={() => setDialog(true)}
      >
        + สายทดสอบ⚙
      </button>
      <TestCallDialog
        open={dialog}
        onClose={() => setDialog(false)}
        onPlaced={onPlaced}
        defaultIntent={intent}
        skills={skills}
        offerable={!disabled}
      />
    </>
  );
}
