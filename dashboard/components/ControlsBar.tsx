"use client";

import type { Controls } from "@/lib/api";

const BUSY_STEPS = [0, 10, 20, 30];

export function ControlsBar({
  controls,
  onChange,
}: {
  controls: Controls;
  onChange: (change: Partial<Controls>) => void;
}) {
  // A value set elsewhere that is not one of the steps still shows as chosen.
  const steps = BUSY_STEPS.includes(controls.busy_extra_minutes)
    ? BUSY_STEPS
    : [...BUSY_STEPS, controls.busy_extra_minutes].sort((a, b) => a - b);

  return (
    <div className="controls">
      <button
        className={controls.accepting_orders ? "control" : "control paused"}
        onClick={() => onChange({ accepting_orders: !controls.accepting_orders })}
      >
        {controls.accepting_orders ? "Pause ordering" : "Resume ordering"}
      </button>
      <div className="segmented" role="group" aria-label="Busy mode">
        <span className="segmented-label">Busy</span>
        {steps.map((minutes) => (
          <button
            key={minutes}
            className={minutes === controls.busy_extra_minutes ? "active" : ""}
            aria-pressed={minutes === controls.busy_extra_minutes}
            onClick={() => onChange({ busy_extra_minutes: minutes })}
          >
            {minutes === 0 ? "Off" : `+${minutes}`}
          </button>
        ))}
      </div>
    </div>
  );
}
