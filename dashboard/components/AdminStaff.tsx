"use client";

import { useCallback, useState } from "react";

import type { Role, StaffApi, StaffChange, StaffMember } from "@/lib/api";
import { useAttempt, useLoaded } from "@/lib/hooks";

import { Dialog, Field } from "./AdminParts";

const PIN = /^\d{6,8}$/;

export function AdminStaff({
  api,
  onSignedOut,
}: {
  api: StaffApi;
  onSignedOut: () => void;
}) {
  const load = useCallback(() => api.admin.staff(), [api]);
  const { data: staff, failed, reload } = useLoaded(load, onSignedOut);
  // The member being edited, or "new".
  const [editing, setEditing] = useState<StaffMember | "new" | null>(null);

  if (staff === null) {
    return <p className="empty">{failed ? "Could not load the staff." : "Loading staff…"}</p>;
  }

  return (
    <section className="panel">
      <header>
        <h2>Staff</h2>
        <button className="primary" onClick={() => setEditing("new")}>
          Add staff
        </button>
      </header>
      <ul className="rows">
        {staff.map((member) => (
          <li key={member.id} className={member.is_active ? "" : "inactive"}>
            <span className="grow">{member.name}</span>
            <span className="muted">
              {member.role === "manager" ? "Manager" : "Staff"}
              {member.is_active ? "" : " · turned off"}
            </span>
            <button onClick={() => setEditing(member)}>Edit</button>
          </li>
        ))}
      </ul>
      {editing !== null && (
        <StaffDialog
          api={api}
          member={editing === "new" ? null : editing}
          onSignedOut={onSignedOut}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            reload();
          }}
        />
      )}
    </section>
  );
}

function StaffDialog({
  api,
  member,
  onSignedOut,
  onClose,
  onSaved,
}: {
  api: StaffApi;
  member: StaffMember | null;
  onSignedOut: () => void;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(member?.name ?? "");
  const [role, setRole] = useState<Role>(member?.role ?? "staff");
  const [pin, setPin] = useState("");
  const [active, setActive] = useState(member?.is_active ?? true);
  const { error, busy, attempt } = useAttempt(onSignedOut);

  // A PIN is needed for someone new and for someone being turned back on.
  const pinNeeded = member === null || (active && !member.is_active);
  const valid = name.trim() !== "" && (pin === "" ? !pinNeeded : PIN.test(pin));

  const save = async () => {
    const saved = await attempt(() => {
      if (member === null) return api.admin.addStaff({ name, pin, role });
      const change: StaffChange = { name, role, is_active: active };
      if (pin !== "") change.pin = pin;
      return api.admin.changeStaff(member.id, change);
    });
    if (saved) onSaved();
  };

  return (
    <Dialog
      title={member === null ? "Add staff" : `Edit ${member.name}`}
      error={error}
      onClose={onClose}
      footer={
        <>
          <button className="quiet" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={!valid || busy} onClick={save}>
            Save
          </button>
        </>
      }
    >
      <Field label="Name">
        <input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} />
      </Field>
      <Field label="Role" hint="A manager can also change staff, the menu, hours and settings.">
        <select value={role} onChange={(event) => setRole(event.target.value as Role)}>
          <option value="staff">Staff</option>
          <option value="manager">Manager</option>
        </select>
      </Field>
      <Field
        label={member === null ? "PIN" : "New PIN"}
        hint={
          member === null
            ? "6 to 8 digits. They sign in with it."
            : "6 to 8 digits. Leave empty to keep the PIN. A new PIN signs them out."
        }
      >
        <input
          value={pin}
          inputMode="numeric"
          autoComplete="off"
          maxLength={8}
          onChange={(event) => setPin(event.target.value.replace(/\D/g, ""))}
        />
      </Field>
      {member !== null && (
        <label className="check">
          <input
            type="checkbox"
            checked={active}
            onChange={(event) => setActive(event.target.checked)}
          />
          <span>Can sign in</span>
        </label>
      )}
    </Dialog>
  );
}
