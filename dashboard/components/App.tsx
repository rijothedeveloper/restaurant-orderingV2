"use client";

import { useCallback, useMemo, useState } from "react";

import { staffApi } from "@/lib/api";
import { useWakeLock } from "@/lib/hooks";
import {
  loadSession,
  restaurantId as findRestaurantId,
  saveSession,
  type StoredSession,
} from "@/lib/session";

import { Dashboard } from "./Dashboard";
import { PinPad } from "./PinPad";

export function App() {
  const [restaurantId] = useState(findRestaurantId);
  if (restaurantId === null) {
    return (
      <main className="centered">
        <h1>Kitchen Dashboard</h1>
        <p>
          Open the dashboard with your restaurant&apos;s link, which ends in{" "}
          <code>?r=</code> and the restaurant id.
        </p>
      </main>
    );
  }
  return <SignedInOrOut restaurantId={restaurantId} />;
}

function SignedInOrOut({ restaurantId }: { restaurantId: string }) {
  const [session, setSession] = useState(() => loadSession(restaurantId));
  useWakeLock();

  const store = useCallback(
    (next: StoredSession | null) => {
      saveSession(restaurantId, next);
      setSession(next);
    },
    [restaurantId],
  );
  const signOut = useCallback(() => store(null), [store]);
  const api = useMemo(
    () => (session === null ? null : staffApi(restaurantId, session.token)),
    [restaurantId, session],
  );

  if (session === null || api === null) {
    return <PinPad restaurantId={restaurantId} onSignedIn={store} />;
  }
  return <Dashboard api={api} staffName={session.name} onSignedOut={signOut} />;
}
