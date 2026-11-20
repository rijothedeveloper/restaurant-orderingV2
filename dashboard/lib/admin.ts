// What the manager screens read and show, kept free of React so it can be tested.

import type { EditableGroup } from "./api";

export const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

/** Dollars as typed ("10", "10.5", "$10.99") in cents, or null if not money. */
export function parseMoney(text: string): number | null {
  const match = /^\$?(\d{1,5})(?:\.(\d{1,2}))?$/.exec(text.trim());
  if (match === null) return null;
  return Number(match[1]) * 100 + Number((match[2] ?? "").padEnd(2, "0"));
}

/** Cents as the text of a price field: 1099 is "10.99". */
export function moneyInput(cents: number): string {
  return (cents / 100).toFixed(2);
}

/** A percentage as typed ("8.75", "9%") in basis points, or null. */
export function parsePercent(text: string): number | null {
  const match = /^(\d{1,3})(?:\.(\d{1,2}))?\s*%?$/.exec(text.trim());
  if (match === null) return null;
  const bps = Number(match[1]) * 100 + Number((match[2] ?? "").padEnd(2, "0"));
  return bps <= 10_000 ? bps : null;
}

/** Basis points as the text of a percentage field: 875 is "8.75". */
export function percentInput(bps: number): string {
  return String(bps / 100);
}

/** A whole number from `min` to `max` as typed, or null. */
export function parseWhole(text: string, min: number, max: number): number | null {
  if (!/^\d+$/.test(text.trim())) return null;
  const value = Number(text);
  return value >= min && value <= max ? value : null;
}

/** Comma-separated aliases, trimmed, without blanks or repeats. */
export function parseAliases(text: string): string[] {
  const seen = new Set<string>();
  const aliases = [];
  for (const part of text.split(",")) {
    const alias = part.trim();
    if (alias === "" || seen.has(alias.toLowerCase())) continue;
    seen.add(alias.toLowerCase());
    aliases.push(alias);
  }
  return aliases;
}

/** The list with the entry at `index` moved by `delta` places, if it can be. */
export function moved<T>(list: T[], index: number, delta: number): T[] {
  const target = index + delta;
  if (target < 0 || target >= list.length) return list;
  const next = [...list];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

/** What a customer must choose from a group, in a few words. */
export function groupRule(group: Pick<EditableGroup, "min_selections" | "max_selections">) {
  const { min_selections: min, max_selections: max } = group;
  if (min === 0) return max === 1 ? "Optional, choose one" : `Optional, up to ${max}`;
  if (min === max) return `Required, choose ${min}`;
  return `Required, choose ${min} to ${max}`;
}

/** "09:00:00" from the API as the "09:00" a time field takes. */
export function timeInput(time: string): string {
  return time.slice(0, 5);
}
