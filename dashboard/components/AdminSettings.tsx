"use client";

import { useCallback, useState } from "react";

import type { OpenHours, RestaurantSetup, StaffApi } from "@/lib/api";
import {
  WEEKDAYS,
  parsePercent,
  parseWhole,
  percentInput,
  timeInput,
} from "@/lib/admin";
import { useAttempt, useLoaded } from "@/lib/hooks";

import { Field } from "./AdminParts";

export function AdminSettings({
  api,
  onSignedOut,
}: {
  api: StaffApi;
  onSignedOut: () => void;
}) {
  const load = useCallback(() => api.admin.setup(), [api]);
  const { data: setup, failed } = useLoaded(load, onSignedOut);

  if (setup === null) {
    return (
      <p className="empty">{failed ? "Could not load the settings." : "Loading settings…"}</p>
    );
  }
  return (
    <>
      <HoursForm api={api} setup={setup} onSignedOut={onSignedOut} />
      <SettingsForm api={api} setup={setup} onSignedOut={onSignedOut} />
    </>
  );
}

function HoursForm({
  api,
  setup,
  onSignedOut,
}: {
  api: StaffApi;
  setup: RestaurantSetup;
  onSignedOut: () => void;
}) {
  const [hours, setHours] = useState<OpenHours[]>(() =>
    setup.hours.map((window) => ({
      weekday: window.weekday,
      opens_at: timeInput(window.opens_at),
      closes_at: timeInput(window.closes_at),
    })),
  );
  const [saved, setSaved] = useState(true);
  const { error, busy, attempt } = useAttempt(onSignedOut);

  const change = (next: OpenHours[]) => {
    setHours(next);
    setSaved(false);
  };
  const edit = (index: number, part: Partial<OpenHours>) =>
    change(hours.map((window, at) => (at === index ? { ...window, ...part } : window)));

  const complete = hours.every((window) => window.opens_at !== "" && window.closes_at !== "");
  const save = async () => {
    if (await attempt(() => api.admin.setHours(hours))) setSaved(true);
  };

  return (
    <section className="panel">
      <header>
        <h2>Opening hours</h2>
        <span className="muted">
          {setup.restaurant_name} · times in {setup.timezone}
        </span>
      </header>
      <ul className="rows">
        {WEEKDAYS.map((day, weekday) => (
          <li key={day} className="day">
            <span className="day-name">{day}</span>
            <div className="grow windows">
              {hours.every((window) => window.weekday !== weekday) && (
                <span className="muted">Closed</span>
              )}
              {hours.map(
                (window, index) =>
                  window.weekday === weekday && (
                    <div key={index} className="window">
                      <input
                        type="time"
                        aria-label={`${day} opens`}
                        value={window.opens_at}
                        onChange={(event) => edit(index, { opens_at: event.target.value })}
                      />
                      <span>to</span>
                      <input
                        type="time"
                        aria-label={`${day} closes`}
                        value={window.closes_at}
                        onChange={(event) => edit(index, { closes_at: event.target.value })}
                      />
                      <button
                        className="quiet icon"
                        aria-label={`Remove ${day} hours`}
                        onClick={() => change(hours.filter((_, at) => at !== index))}
                      >
                        ✕
                      </button>
                    </div>
                  ),
              )}
            </div>
            <button
              onClick={() =>
                change([...hours, { weekday, opens_at: "09:00", closes_at: "21:00" }])
              }
            >
              Add hours
            </button>
          </li>
        ))}
      </ul>
      <p className="muted">
        A closing time at or before the opening time means it closes the next day.
      </p>
      {error !== null && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <footer>
        <button className="primary" disabled={saved || busy || !complete} onClick={save}>
          {saved ? "Hours saved" : "Save hours"}
        </button>
      </footer>
    </section>
  );
}

function SettingsForm({
  api,
  setup,
  onSignedOut,
}: {
  api: StaffApi;
  setup: RestaurantSetup;
  onSignedOut: () => void;
}) {
  const [tax, setTax] = useState(percentInput(setup.settings.tax_rate_bps));
  const [capacity, setCapacity] = useState(String(setup.settings.kitchen_capacity));
  const [lastOrder, setLastOrder] = useState(String(setup.settings.last_order_minutes));
  const [saved, setSaved] = useState(true);
  const { error, busy, attempt } = useAttempt(onSignedOut);

  const settings = {
    tax_rate_bps: parsePercent(tax),
    kitchen_capacity: parseWhole(capacity, 1, 100),
    last_order_minutes: parseWhole(lastOrder, 0, 240),
  };
  const typed = (set: (text: string) => void) => (text: string) => {
    set(text);
    setSaved(false);
  };

  const save = async () => {
    const { tax_rate_bps, kitchen_capacity, last_order_minutes } = settings;
    if (tax_rate_bps === null || kitchen_capacity === null || last_order_minutes === null) {
      return;
    }
    const sent = await attempt(() =>
      api.admin.setSettings({ tax_rate_bps, kitchen_capacity, last_order_minutes }),
    );
    if (sent) setSaved(true);
  };

  return (
    <section className="panel">
      <header>
        <h2>Settings</h2>
      </header>
      <div className="fields">
        <Field label="Sales tax (%)" hint="Used for new orders; placed orders keep theirs.">
          <input
            value={tax}
            inputMode="decimal"
            onChange={(event) => typed(setTax)(event.target.value)}
          />
        </Field>
        <Field
          label="Kitchen capacity"
          hint="Orders the kitchen finishes in 10 minutes. Sets the wait quoted when busy."
        >
          <input
            value={capacity}
            inputMode="numeric"
            onChange={(event) => typed(setCapacity)(event.target.value)}
          />
        </Field>
        <Field
          label="Last order (minutes before closing)"
          hint="No new orders this close to closing time."
        >
          <input
            value={lastOrder}
            inputMode="numeric"
            onChange={(event) => typed(setLastOrder)(event.target.value)}
          />
        </Field>
      </div>
      {error !== null && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <footer>
        <button
          className="primary"
          disabled={saved || busy || Object.values(settings).includes(null)}
          onClick={save}
        >
          {saved ? "Settings saved" : "Save settings"}
        </button>
      </footer>
    </section>
  );
}
