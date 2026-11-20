"use client";

import { useState } from "react";

import { ApiError, login } from "@/lib/api";
import type { StoredSession } from "@/lib/session";
import { unlockSound } from "@/lib/sound";

const MIN_PIN = 6;
const MAX_PIN = 8;
const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];

export function PinPad({
  restaurantId,
  onSignedIn,
}: {
  restaurantId: string;
  onSignedIn: (session: StoredSession) => void;
}) {
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const press = (digit: string) => {
    unlockSound();
    setError(null);
    setPin((current) => (current.length < MAX_PIN ? current + digit : current));
  };

  const submit = async () => {
    setBusy(true);
    try {
      const session = await login(restaurantId, pin);
      onSignedIn({
        token: session.token,
        name: session.staff.name,
        role: session.staff.role,
      });
    } catch (caught) {
      setPin("");
      setError(loginMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="centered">
      <h1>Kitchen Dashboard</h1>
      <p>Enter your PIN</p>
      <div className="pin-dots" aria-label={`${pin.length} digits entered`}>
        {Array.from({ length: MAX_PIN }, (_, index) => (
          <span key={index} className={index < pin.length ? "dot filled" : "dot"} />
        ))}
      </div>
      <p className="pin-error" role="alert">
        {error}
      </p>
      <div className="pin-keys">
        {KEYS.map((digit) => (
          <button key={digit} onClick={() => press(digit)} disabled={busy}>
            {digit}
          </button>
        ))}
        <button
          className="quiet"
          onClick={() => setPin((current) => current.slice(0, -1))}
          disabled={busy || pin.length === 0}
          aria-label="Delete last digit"
        >
          ⌫
        </button>
        <button onClick={() => press("0")} disabled={busy}>
          0
        </button>
        <button
          className="primary"
          onClick={submit}
          disabled={busy || pin.length < MIN_PIN}
        >
          {busy ? "…" : "Sign in"}
        </button>
      </div>
    </main>
  );
}

function loginMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Wrong PIN. Try again.";
    if (error.status === 429) {
      const minutes = Math.ceil((error.retryAfter ?? 300) / 60);
      return `Too many wrong PINs. Try again in ${minutes} min.`;
    }
    return error.message;
  }
  return "Cannot reach the server. Check the connection.";
}
