"use client";

import { useCallback, useState } from "react";

import type {
  EditableCategory,
  EditableGroup,
  EditableItem,
  EditableMenu,
  OptionKind,
  StaffApi,
} from "@/lib/api";
import {
  groupRule,
  moneyInput,
  moved,
  parseAliases,
  parseMoney,
  parseWhole,
} from "@/lib/admin";
import { money } from "@/lib/board";
import { useAttempt, useLoaded } from "@/lib/hooks";

import { Dialog, Field, MoveButtons } from "./AdminParts";

type Editing =
  | { kind: "category"; category: EditableCategory | null }
  | { kind: "item"; item: EditableItem | null; categoryId: string }
  | { kind: "group"; group: EditableGroup | null };

export function AdminMenu({ api, onSignedOut }: { api: StaffApi; onSignedOut: () => void }) {
  const load = useCallback(() => api.admin.menu(), [api]);
  const { data: menu, setData: setMenu, failed } = useLoaded(load, onSignedOut);
  const [editing, setEditing] = useState<Editing | null>(null);
  const { error, busy, attempt } = useAttempt(onSignedOut);

  if (menu === null) {
    return <p className="empty">{failed ? "Could not load the menu." : "Loading menu…"}</p>;
  }

  /** For the buttons on the page itself; dialogs run their own requests. */
  const change = (request: () => Promise<EditableMenu>) =>
    attempt(async () => setMenu(await request()));
  const saved = (next: EditableMenu) => {
    setMenu(next);
    setEditing(null);
  };
  const dialog = { api, menu, onSignedOut, onSaved: saved, onClose: () => setEditing(null) };

  return (
    <>
      {error !== null && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <section className="panel">
        <header>
          <h2>Menu</h2>
          <button
            className="primary"
            onClick={() => setEditing({ kind: "category", category: null })}
          >
            Add category
          </button>
        </header>
        {menu.categories.length === 0 && (
          <p className="muted">No categories yet. Add one, then add items to it.</p>
        )}
        {menu.categories.map((category, index) => (
          <div key={category.id} className="category">
            <div className="row heading">
              <h3 className="grow">{category.name}</h3>
              <MoveButtons
                name={category.name}
                index={index}
                count={menu.categories.length}
                disabled={busy}
                onMove={(delta) =>
                  change(() =>
                    api.admin.orderCategories(
                      moved(menu.categories, index, delta).map((entry) => entry.id),
                    ),
                  )
                }
              />
              <button onClick={() => setEditing({ kind: "category", category })}>
                Rename
              </button>
              <button
                className="danger"
                disabled={busy || category.items.length > 0}
                title={category.items.length > 0 ? "Move or delete its items first" : ""}
                onClick={() => {
                  if (window.confirm(`Delete the category ${category.name}?`)) {
                    change(() => api.admin.deleteCategory(category.id));
                  }
                }}
              >
                Delete
              </button>
            </div>
            <ul className="rows">
              {category.items.map((item, at) => (
                <li key={item.id}>
                  <span className="grow">
                    {item.name}
                    {!item.is_available && <span className="sold-out"> · sold out</span>}
                  </span>
                  <span className="muted">
                    {money(item.price_cents)} · {item.prep_minutes} min
                  </span>
                  <MoveButtons
                    name={item.name}
                    index={at}
                    count={category.items.length}
                    disabled={busy}
                    onMove={(delta) =>
                      change(() =>
                        api.admin.orderItems(
                          category.id,
                          moved(category.items, at, delta).map((entry) => entry.id),
                        ),
                      )
                    }
                  />
                  <button
                    onClick={() =>
                      setEditing({ kind: "item", item, categoryId: category.id })
                    }
                  >
                    Edit
                  </button>
                </li>
              ))}
            </ul>
            <button
              onClick={() =>
                setEditing({ kind: "item", item: null, categoryId: category.id })
              }
            >
              Add item to {category.name}
            </button>
          </div>
        ))}
      </section>

      <section className="panel">
        <header>
          <h2>Option groups</h2>
          <button
            className="primary"
            onClick={() => setEditing({ kind: "group", group: null })}
          >
            Add option group
          </button>
        </header>
        <p className="muted">
          Choices and extras a customer can pick for an item, such as a size. One group
          can be used by several items; choose an item&apos;s groups when you edit it.
        </p>
        <ul className="rows">
          {menu.groups.map((group) => (
            <li key={group.id}>
              <span className="grow">
                {group.name}
                <span className="muted"> · {groupRule(group)}</span>
                <br />
                <span className="muted">
                  {group.options.map((option) => option.name).join(", ") || "No options"}
                </span>
              </span>
              <button onClick={() => setEditing({ kind: "group", group })}>Edit</button>
            </li>
          ))}
        </ul>
      </section>

      {editing?.kind === "category" && (
        <CategoryDialog {...dialog} category={editing.category} />
      )}
      {editing?.kind === "item" && (
        <ItemDialog {...dialog} item={editing.item} categoryId={editing.categoryId} />
      )}
      {editing?.kind === "group" && <GroupDialog {...dialog} group={editing.group} />}
    </>
  );
}

interface DialogProps {
  api: StaffApi;
  menu: EditableMenu;
  onSignedOut: () => void;
  onSaved: (menu: EditableMenu) => void;
  onClose: () => void;
}

function SaveButtons({
  disabled,
  onClose,
  onSave,
  onDelete,
}: {
  disabled: boolean;
  onClose: () => void;
  onSave: () => void;
  onDelete?: () => void;
}) {
  return (
    <>
      {onDelete !== undefined && (
        <button className="danger left" onClick={onDelete}>
          Delete
        </button>
      )}
      <button className="quiet" onClick={onClose}>
        Cancel
      </button>
      <button className="primary" disabled={disabled} onClick={onSave}>
        Save
      </button>
    </>
  );
}

function CategoryDialog({
  api,
  category,
  onSignedOut,
  onSaved,
  onClose,
}: DialogProps & { category: EditableCategory | null }) {
  const [name, setName] = useState(category?.name ?? "");
  const { error, busy, attempt } = useAttempt(onSignedOut);

  const save = () =>
    attempt(async () =>
      onSaved(
        category === null
          ? await api.admin.addCategory(name)
          : await api.admin.renameCategory(category.id, name),
      ),
    );

  return (
    <Dialog
      title={category === null ? "Add category" : `Rename ${category.name}`}
      error={error}
      onClose={onClose}
      footer={
        <SaveButtons disabled={busy || name.trim() === ""} onClose={onClose} onSave={save} />
      }
    >
      <Field label="Name">
        <input
          value={name}
          maxLength={80}
          autoFocus
          onChange={(event) => setName(event.target.value)}
        />
      </Field>
    </Dialog>
  );
}

function ItemDialog({
  api,
  menu,
  item,
  categoryId,
  onSignedOut,
  onSaved,
  onClose,
}: DialogProps & { item: EditableItem | null; categoryId: string }) {
  const [name, setName] = useState(item?.name ?? "");
  const [description, setDescription] = useState(item?.description ?? "");
  const [price, setPrice] = useState(item === null ? "" : moneyInput(item.price_cents));
  const [prep, setPrep] = useState(String(item?.prep_minutes ?? 10));
  const [category, setCategory] = useState(categoryId);
  const [aliases, setAliases] = useState(item?.aliases.join(", ") ?? "");
  // In the order the customer is asked; a newly ticked group goes last.
  const [groupIds, setGroupIds] = useState(item?.group_ids ?? []);
  const { error, busy, attempt } = useAttempt(onSignedOut);

  const priceCents = parseMoney(price);
  const prepMinutes = parseWhole(prep, 0, 240);
  const valid = name.trim() !== "" && priceCents !== null && prepMinutes !== null;

  const save = () => {
    if (priceCents === null || prepMinutes === null) return;
    const input = {
      category_id: category,
      name,
      description: description.trim() === "" ? null : description,
      price_cents: priceCents,
      prep_minutes: prepMinutes,
      aliases: parseAliases(aliases),
      group_ids: groupIds,
    };
    attempt(async () =>
      onSaved(
        item === null
          ? await api.admin.addItem(input)
          : await api.admin.changeItem(item.id, input),
      ),
    );
  };
  const remove =
    item === null
      ? undefined
      : () => {
          if (window.confirm(`Delete ${item.name} from the menu?`)) {
            attempt(async () => onSaved(await api.admin.deleteItem(item.id)));
          }
        };

  return (
    <Dialog
      title={item === null ? "Add item" : `Edit ${item.name}`}
      error={error}
      onClose={onClose}
      footer={
        <SaveButtons
          disabled={busy || !valid}
          onClose={onClose}
          onSave={save}
          onDelete={remove}
        />
      }
    >
      <Field label="Name">
        <input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} />
      </Field>
      <Field label="Description">
        <textarea
          value={description}
          maxLength={500}
          rows={2}
          onChange={(event) => setDescription(event.target.value)}
        />
      </Field>
      <div className="fields">
        <Field label="Price ($)">
          <input
            value={price}
            inputMode="decimal"
            placeholder="0.00"
            onChange={(event) => setPrice(event.target.value)}
          />
        </Field>
        <Field label="Prep time (minutes)">
          <input
            value={prep}
            inputMode="numeric"
            onChange={(event) => setPrep(event.target.value)}
          />
        </Field>
        <Field label="Category">
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            {menu.categories.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field
        label="Also called"
        hint="Other names customers use, separated by commas: coke, cola"
      >
        <input value={aliases} onChange={(event) => setAliases(event.target.value)} />
      </Field>
      {menu.groups.length > 0 && (
        <fieldset>
          <legend>Option groups</legend>
          {menu.groups.map((group) => (
            <label key={group.id} className="check">
              <input
                type="checkbox"
                checked={groupIds.includes(group.id)}
                onChange={(event) =>
                  setGroupIds(
                    event.target.checked
                      ? [...groupIds, group.id]
                      : groupIds.filter((id) => id !== group.id),
                  )
                }
              />
              <span>
                {group.name} <span className="muted">· {groupRule(group)}</span>
              </span>
            </label>
          ))}
        </fieldset>
      )}
    </Dialog>
  );
}

interface OptionRow {
  // Set for an option the group already has.
  id?: string;
  // Stable while the dialog is open, for React.
  key: number;
  name: string;
  kind: OptionKind;
  price: string;
}

function GroupDialog({
  api,
  group,
  onSignedOut,
  onSaved,
  onClose,
}: DialogProps & { group: EditableGroup | null }) {
  const [name, setName] = useState(group?.name ?? "");
  const [min, setMin] = useState(String(group?.min_selections ?? 0));
  const [max, setMax] = useState(String(group?.max_selections ?? 1));
  const [rows, setRows] = useState<OptionRow[]>(() =>
    (group?.options ?? []).map((option, key) => ({
      id: option.id,
      key,
      name: option.name,
      kind: option.kind,
      price: moneyInput(option.price_delta_cents),
    })),
  );
  const [nextKey, setNextKey] = useState(rows.length);
  const { error, busy, attempt } = useAttempt(onSignedOut);

  const edit = (key: number, part: Partial<OptionRow>) =>
    setRows(rows.map((row) => (row.key === key ? { ...row, ...part } : row)));
  const addRow = () => {
    setRows([...rows, { key: nextKey, name: "", kind: "choice", price: "0.00" }]);
    setNextKey(nextKey + 1);
  };

  const minSelections = parseWhole(min, 0, 20);
  const maxSelections = parseWhole(max, 1, 20);
  const options = rows.map((row) => ({
    id: row.id,
    name: row.name.trim(),
    kind: row.kind,
    price_delta_cents: row.kind === "remove" ? 0 : parseMoney(row.price),
  }));
  const problem =
    name.trim() === ""
      ? "Give the group a name."
      : minSelections === null || maxSelections === null
        ? "Choose at least 0 and at most 1 to 20."
        : maxSelections < minSelections
          ? "At most cannot be less than at least."
          : options.some((option) => option.name === "" || option.price_delta_cents === null)
            ? "Every option needs a name and a price."
            : new Set(options.map((option) => option.name)).size < options.length
              ? "Two options have the same name."
              : minSelections > options.length
                ? "The group needs more options than it has."
                : null;

  const save = () => {
    if (problem !== null || minSelections === null || maxSelections === null) return;
    const input = {
      name,
      min_selections: minSelections,
      max_selections: maxSelections,
      options: options.map((option) => ({
        ...option,
        price_delta_cents: option.price_delta_cents ?? 0,
      })),
    };
    attempt(async () =>
      onSaved(
        group === null
          ? await api.admin.addGroup(input)
          : await api.admin.changeGroup(group.id, input),
      ),
    );
  };
  const remove =
    group === null
      ? undefined
      : () => {
          if (window.confirm(`Delete ${group.name}? It comes off every item that uses it.`)) {
            attempt(async () => onSaved(await api.admin.deleteGroup(group.id)));
          }
        };

  return (
    <Dialog
      title={group === null ? "Add option group" : `Edit ${group.name}`}
      error={error ?? problem}
      onClose={onClose}
      footer={
        <SaveButtons
          disabled={busy || problem !== null}
          onClose={onClose}
          onSave={save}
          onDelete={remove}
        />
      }
    >
      <Field label="Name" hint="Such as Size, Style or Extras.">
        <input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} />
      </Field>
      <div className="fields">
        <Field label="Choose at least" hint="0 makes the group optional.">
          <input value={min} inputMode="numeric" onChange={(event) => setMin(event.target.value)} />
        </Field>
        <Field label="Choose at most">
          <input value={max} inputMode="numeric" onChange={(event) => setMax(event.target.value)} />
        </Field>
      </div>
      <fieldset>
        <legend>Options</legend>
        {rows.map((row, index) => (
          <div key={row.key} className="option-row">
            <input
              className="grow"
              aria-label="Option name"
              placeholder="Name"
              value={row.name}
              maxLength={80}
              onChange={(event) => edit(row.key, { name: event.target.value })}
            />
            <select
              aria-label="Option type"
              value={row.kind}
              onChange={(event) => edit(row.key, { kind: event.target.value as OptionKind })}
            >
              <option value="choice">Choice</option>
              <option value="add">Add</option>
              <option value="remove">Remove</option>
            </select>
            <input
              className="price"
              aria-label="Extra price in dollars"
              inputMode="decimal"
              disabled={row.kind === "remove"}
              value={row.kind === "remove" ? "0.00" : row.price}
              onChange={(event) => edit(row.key, { price: event.target.value })}
            />
            <MoveButtons
              name={row.name || "option"}
              index={index}
              count={rows.length}
              onMove={(delta) => setRows(moved(rows, index, delta))}
            />
            <button
              className="quiet icon"
              aria-label={`Remove ${row.name || "option"}`}
              onClick={() => setRows(rows.filter((other) => other.key !== row.key))}
            >
              ✕
            </button>
          </div>
        ))}
        <button onClick={addRow}>Add option</button>
      </fieldset>
    </Dialog>
  );
}
