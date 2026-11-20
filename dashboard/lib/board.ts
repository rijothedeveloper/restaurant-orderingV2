// What the board shows for an order, kept free of React so it can be tested.

import type { Order, OrderStatus, StaffAction } from "./api";

export const COLUMNS: { status: OrderStatus; title: string }[] = [
  { status: "placed", title: "New" },
  { status: "accepted", title: "Accepted" },
  { status: "preparing", title: "Preparing" },
  { status: "ready", title: "Ready" },
];

/** A no-show is offered this long after the order became ready. */
export const NO_SHOW_AFTER_MINUTES = 30;

const NEXT_STEP: Partial<Record<OrderStatus, { action: StaffAction; label: string }>> = {
  placed: { action: "accept", label: "Accept" },
  accepted: { action: "prepare", label: "Start preparing" },
  preparing: { action: "ready", label: "Mark ready" },
  ready: { action: "picked_up", label: "Picked up & paid" },
};

/** The one-tap action that moves an order forward. */
export function nextStep(status: OrderStatus) {
  return NEXT_STEP[status] ?? null;
}

/** The way out of an order before cooking starts, if there still is one. */
export function wayOut(status: OrderStatus): "reject" | "cancel" | null {
  if (status === "placed") return "reject";
  if (status === "accepted") return "cancel";
  return null;
}

export function noShowOffered(order: Order, now: number): boolean {
  if (order.status !== "ready") return false;
  const readyFor = now - Date.parse(order.status_changed_at);
  return readyFor >= NO_SHOW_AFTER_MINUTES * 60_000;
}

export function ordersIn(orders: Order[], status: OrderStatus): Order[] {
  return orders.filter((order) => order.status === status);
}

/** New orders chime until someone accepts or rejects them. */
export function needsChime(orders: Order[]): boolean {
  return orders.some((order) => order.status === "placed");
}

export interface PickupTimer {
  label: string;
  tone: "ok" | "soon" | "late";
}

/** Time left until the promised pickup, or how late the order is. */
export function pickupTimer(order: Order, now: number): PickupTimer | null {
  if (order.promised_pickup_at === null) return null;
  const minutes = Math.ceil((Date.parse(order.promised_pickup_at) - now) / 60_000);
  if (order.status === "ready") {
    return minutes >= 0
      ? { label: "on time", tone: "ok" }
      : { label: `waiting ${span(-minutes)}`, tone: "soon" };
  }
  if (minutes < 0) return { label: `${span(-minutes)} late`, tone: "late" };
  if (minutes === 0) return { label: "due now", tone: "late" };
  return { label: `in ${span(minutes)}`, tone: minutes <= 5 ? "soon" : "ok" };
}

function span(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}

/** A clock time in the restaurant's own timezone, such as "12:35 PM". */
export function clockTime(iso: string, timezone: string): string {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: timezone,
  }).format(new Date(iso));
}

export function money(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

const CHANNEL_NAMES: Record<string, string> = {
  voice: "Phone",
  web: "Web chat",
  whatsapp: "WhatsApp",
  sms: "Text",
  storefront: "Online",
};

export function channelName(channel: string): string {
  return CHANNEL_NAMES[channel] ?? channel;
}
