"use client";

import type { ReactNode } from "react";

/** A modal form. Tapping outside it closes it without saving. */
export function Dialog({
  title,
  error,
  onClose,
  children,
  footer,
}: {
  title: string;
  error: string | null;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <div className="scrim" onClick={onClose}>
      <div
        className="dialog form"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <h2>{title}</h2>
        {children}
        {error !== null && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <footer>{footer}</footer>
      </div>
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint !== undefined && <small>{hint}</small>}
    </label>
  );
}

/** Up and down buttons for one row of a list. */
export function MoveButtons({
  name,
  index,
  count,
  disabled,
  onMove,
}: {
  name: string;
  index: number;
  count: number;
  disabled?: boolean;
  onMove: (delta: number) => void;
}) {
  return (
    <>
      <button
        type="button"
        className="quiet icon"
        aria-label={`Move ${name} up`}
        disabled={disabled || index === 0}
        onClick={() => onMove(-1)}
      >
        ↑
      </button>
      <button
        type="button"
        className="quiet icon"
        aria-label={`Move ${name} down`}
        disabled={disabled || index === count - 1}
        onClick={() => onMove(1)}
      >
        ↓
      </button>
    </>
  );
}
