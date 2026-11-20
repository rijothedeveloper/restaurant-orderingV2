"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { ApiError, type Board, type StaffApi } from "./api";
import { needsChime } from "./board";
import { chime } from "./sound";

const POLL_MS = 5_000;
const CHIME_MS = 3_000;

const subscribeNever = () => () => {};

/** False while prerendering and hydrating, true in the browser afterwards. */
export function useIsClient(): boolean {
  return useSyncExternalStore(
    subscribeNever,
    () => true,
    () => false,
  );
}

/** The current time, refreshed often enough for minute timers. */
export function useNow(everyMs = 10_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(timer);
  }, [everyMs]);
  return now;
}

/**
 * Polls the board every 5 seconds. `failedSince` is when polling started
 * failing, so the screen can say its orders may be out of date.
 */
export function useBoard(api: StaffApi, onSignedOut: () => void) {
  const [board, setBoard] = useState<Board | null>(null);
  const [failedSince, setFailedSince] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await api.board();
      setBoard(next);
      setFailedSince(null);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        onSignedOut();
      } else {
        setFailedSince((since) => since ?? Date.now());
      }
    }
  }, [api, onSignedOut]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await refresh();
      if (!stopped) timer = setTimeout(poll, POLL_MS);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [refresh]);

  return { board, failedSince, refresh };
}

/** Chimes at once for a new order, then again until none is left unaccepted. */
export function useChime(board: Board | null): void {
  const ringing = board !== null && needsChime(board.orders);
  useEffect(() => {
    if (!ringing) return;
    chime();
    const timer = setInterval(chime, CHIME_MS);
    return () => clearInterval(timer);
  }, [ringing]);
}

/** Keeps the tablet's screen on while the dashboard is showing. */
export function useWakeLock(): void {
  const lock = useRef<WakeLockSentinel | null>(null);
  useEffect(() => {
    if (!("wakeLock" in navigator)) return;
    const acquire = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        lock.current = await navigator.wakeLock.request("screen");
      } catch {
        // Refused (low battery, for one). The screen may dim; nothing to do.
      }
    };
    void acquire();
    document.addEventListener("visibilitychange", acquire);
    return () => {
      document.removeEventListener("visibilitychange", acquire);
      void lock.current?.release();
    };
  }, []);
}
