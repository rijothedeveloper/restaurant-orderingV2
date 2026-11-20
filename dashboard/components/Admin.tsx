"use client";

import { useState } from "react";

import type { StaffApi } from "@/lib/api";

import { AdminMenu } from "./AdminMenu";
import { AdminSettings } from "./AdminSettings";
import { AdminStaff } from "./AdminStaff";

const SCREENS = [
  { key: "menu", title: "Menu" },
  { key: "staff", title: "Staff" },
  { key: "settings", title: "Hours & settings" },
] as const;

type Screen = (typeof SCREENS)[number]["key"];

/** The manager's screens. The API refuses them to anyone else. */
export function Admin({ api, onSignedOut }: { api: StaffApi; onSignedOut: () => void }) {
  const [screen, setScreen] = useState<Screen>("menu");

  return (
    <main className="admin">
      <nav className="subtabs">
        {SCREENS.map(({ key, title }) => (
          <button
            key={key}
            className={screen === key ? "tab active" : "tab"}
            onClick={() => setScreen(key)}
          >
            {title}
          </button>
        ))}
      </nav>
      {screen === "menu" && <AdminMenu api={api} onSignedOut={onSignedOut} />}
      {screen === "staff" && <AdminStaff api={api} onSignedOut={onSignedOut} />}
      {screen === "settings" && <AdminSettings api={api} onSignedOut={onSignedOut} />}
    </main>
  );
}
