/**
 * The live link to the server, and the only stateful thing in the client.
 *
 * The contract it upholds (`api/realtime.py`):
 *
 * * remember the highest `seq` applied, and send it as `last_seq` on reconnect, so the
 *   server replays exactly the gap rather than everything or nothing;
 * * ignore anything with a `seq` we have already applied — a duplicate offer is a call
 *   ringing at a desk that already answered it;
 * * treat the socket as a *view*. If it drops, the call carries on; we re-fetch the
 *   snapshot over REST and carry on with it.
 *
 * Reconnect backs off, because the failure this must survive is the server restarting
 * during a demo, and twenty tabs hammering it as it boots is how a slow start becomes a
 * failed one.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { Snapshot } from "./api";

export type SocketMessage = {
  seq: number;
  type: string;
  at?: string;
  payload: Record<string, unknown>;
};

export type SocketStatus = "connecting" | "live" | "offline";

const HEARTBEAT_MS = 10_000;
const BACKOFF_MS = [500, 1000, 2000, 4000, 8000];

export function useSocket(
  enabled: boolean,
  onMessage: (message: SocketMessage) => void,
  onSnapshot: (snapshot: Snapshot) => void,
) {
  const [status, setStatus] = useState<SocketStatus>("offline");
  const socketRef = useRef<WebSocket | null>(null);
  const lastSeq = useRef(0);
  const attempt = useRef(0);
  const stopped = useRef(false);
  // Kept in refs, not deps: re-subscribing the socket every time a parent re-renders
  // would tear down a live connection on every keystroke in the wrap-up box.
  const handleMessage = useRef(onMessage);
  const handleSnapshot = useRef(onSnapshot);
  handleMessage.current = onMessage;
  handleSnapshot.current = onSnapshot;

  const connect = useCallback(() => {
    if (stopped.current) return;
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${window.location.host}/v1/agent/ws`);
    socketRef.current = socket;
    setStatus("connecting");

    socket.onopen = () => {
      attempt.current = 0;
      setStatus("live");
      socket.send(JSON.stringify({ type: "hello", last_seq: lastSeq.current }));
    };

    socket.onmessage = (event) => {
      const message = JSON.parse(event.data) as SocketMessage;
      if (message.type === "snapshot") {
        handleSnapshot.current(message.payload as unknown as Snapshot);
        return;
      }
      // seq 0 is reserved for out-of-band frames (heartbeat_ack, snapshot) that carry no
      // ordering; anything at or below what we have applied is a replay we already saw.
      if (message.seq > 0) {
        if (message.seq <= lastSeq.current) return;
        lastSeq.current = message.seq;
        socket.send(JSON.stringify({ type: "ack", seq: message.seq }));
      }
      handleMessage.current(message);
    };

    socket.onclose = () => {
      setStatus("offline");
      if (stopped.current) return;
      const delay = BACKOFF_MS[Math.min(attempt.current, BACKOFF_MS.length - 1)];
      attempt.current += 1;
      window.setTimeout(connect, delay);
    };

    socket.onerror = () => socket.close();
  }, []);

  useEffect(() => {
    if (!enabled) return;
    stopped.current = false;
    // **Start a new session at zero** (`B27`). `seq` is per AGENT on the server, and this
    // ref is per TAB — so signing out of one agent and into another in the same tab left
    // the high-water mark of the previous agent in place, and every one of the new
    // agent's messages looked like a replay we had already applied. Including the offer.
    // The card then appeared only when something *else* triggered a refresh, which is the
    // ten-second heartbeat, on a twenty-second ring.
    //
    // This resets only when `enabled` flips — a fresh sign-in. A reconnect after a
    // network drop goes through `onclose` -> backoff -> `connect()` without re-running
    // this effect, so it keeps its position and the server still replays the gap, which
    // is the whole point of the outbox (`D68`).
    lastSeq.current = 0;
    connect();
    const sendBeat = () => {
      const socket = socketRef.current;
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "heartbeat" }));
      }
    };
    const beat = window.setInterval(sendBeat, HEARTBEAT_MS);

    // Browsers throttle a hidden tab's timers to roughly one a minute, so this interval
    // is NOT a promise about a backgrounded workstation — an agent who looks at another
    // window stops heartbeating on the server's terms (`B34`). The server no longer drops
    // an agent who is on a call because of it, and this beats immediately on return so
    // presence is correct at once rather than up to ten seconds later.
    const onVisible = () => {
      if (document.visibilityState === "visible") sendBeat();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      stopped.current = true;
      window.clearInterval(beat);
      document.removeEventListener("visibilitychange", onVisible);
      socketRef.current?.close();
    };
  }, [enabled, connect]);

  return status;
}
