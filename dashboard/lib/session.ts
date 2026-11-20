// Where the dashboard keeps its restaurant and sign-in between reloads.

import type { Role } from "./api";

const RESTAURANT_KEY = "dashboard.restaurant";
const sessionKey = (restaurantId: string) => `dashboard.session.${restaurantId}`;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The restaurant from `?r=<id>`, remembered for when the link has none. */
export function restaurantId(): string | null {
  const fromUrl = new URLSearchParams(window.location.search).get("r");
  if (fromUrl !== null && UUID.test(fromUrl)) {
    localStorage.setItem(RESTAURANT_KEY, fromUrl);
    return fromUrl;
  }
  return localStorage.getItem(RESTAURANT_KEY);
}

export interface StoredSession {
  token: string;
  name: string;
  // Decides which screens are offered; the API checks the role itself.
  role: Role;
}

export function loadSession(restaurantId: string): StoredSession | null {
  try {
    const stored = JSON.parse(localStorage.getItem(sessionKey(restaurantId)) ?? "null");
    if (typeof stored?.token === "string" && typeof stored?.name === "string") {
      const role: Role = stored.role === "manager" ? "manager" : "staff";
      return { token: stored.token, name: stored.name, role };
    }
  } catch {
    // Not JSON: treat as signed out.
  }
  return null;
}

export function saveSession(restaurantId: string, session: StoredSession | null): void {
  if (session === null) localStorage.removeItem(sessionKey(restaurantId));
  else localStorage.setItem(sessionKey(restaurantId), JSON.stringify(session));
}
