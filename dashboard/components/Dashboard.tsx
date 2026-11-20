"use client";

import { useCallback, useState } from "react";

import { ApiError, type Controls, type StaffApi } from "@/lib/api";
import { useBoard, useChime, useNow } from "@/lib/hooks";
import { soundReady, unlockSound } from "@/lib/sound";

import { Admin } from "./Admin";
import { BoardView } from "./BoardView";
import { ControlsBar } from "./ControlsBar";
import { SoldOut } from "./SoldOut";

type View = "orders" | "menu" | "admin";

export function Dashboard({
  api,
  staffName,
  isManager,
  onSignedOut,
}: {
  api: StaffApi;
  staffName: string;
  isManager: boolean;
  onSignedOut: () => void;
}) {
  const [view, setView] = useState<View>("orders");
  const [notice, setNotice] = useState<string | null>(null);
  const [soundOn, setSoundOn] = useState(soundReady);
  const { board, failedSince, refresh } = useBoard(api, onSignedOut);
  const now = useNow();
  useChime(board);

  /** Runs a staff request, then refreshes; a failure shows as a notice. */
  const run = useCallback(
    async (request: () => Promise<unknown>) => {
      setNotice(null);
      try {
        await request();
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          onSignedOut();
          return;
        }
        setNotice(
          error instanceof ApiError
            ? error.message
            : "That did not go through. Check the connection and try again.",
        );
      }
      await refresh();
    },
    [onSignedOut, refresh],
  );

  const signOut = async () => {
    await api.logout().catch(() => {});
    onSignedOut();
  };

  const enableSound = () => {
    unlockSound();
    setSoundOn(true);
  };

  const controls: Controls | null = board?.controls ?? null;

  return (
    <div className="dashboard">
      <header className="topbar">
        <nav>
          <button
            className={view === "orders" ? "tab active" : "tab"}
            onClick={() => setView("orders")}
          >
            Orders
          </button>
          <button
            className={view === "menu" ? "tab active" : "tab"}
            onClick={() => setView("menu")}
          >
            Sold out
          </button>
          {isManager && (
            <button
              className={view === "admin" ? "tab active" : "tab"}
              onClick={() => setView("admin")}
            >
              Manage
            </button>
          )}
        </nav>
        {controls !== null && (
          <ControlsBar
            controls={controls}
            onChange={(change) => run(() => api.setControls(change))}
          />
        )}
        <div className="who">
          <span>{staffName}</span>
          <button className="quiet" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>

      {failedSince !== null && (
        <div className="banner danger" role="alert">
          Not connected since {new Date(failedSince).toLocaleTimeString()}. Orders
          shown may be out of date; new orders will not appear until this clears.
        </div>
      )}
      {controls !== null && !controls.accepting_orders && (
        <div className="banner warn">Ordering is paused. No new orders are coming in.</div>
      )}
      {!soundOn && (
        <button className="banner info" onClick={enableSound}>
          Sound is off. Tap here so new orders chime.
        </button>
      )}
      {notice !== null && (
        <button className="banner danger" onClick={() => setNotice(null)}>
          {notice} (tap to dismiss)
        </button>
      )}

      {view === "orders" ? (
        board === null ? (
          <p className="empty">{failedSince === null ? "Loading orders…" : ""}</p>
        ) : (
          <BoardView
            orders={board.orders}
            now={now}
            onAct={(order, action, details) =>
              run(() => api.act(order.id, action, details))
            }
          />
        )
      ) : view === "menu" ? (
        <SoldOut api={api} run={run} />
      ) : (
        <Admin api={api} onSignedOut={onSignedOut} />
      )}
    </div>
  );
}
