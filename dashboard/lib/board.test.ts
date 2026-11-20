import { describe, expect, it } from "vitest";

import type { Order, OrderStatus } from "./api";
import {
  clockTime,
  money,
  needsChime,
  nextStep,
  noShowOffered,
  ordersIn,
  pickupTimer,
  wayOut,
} from "./board";

const NOW = Date.parse("2026-11-18T20:00:00Z");
const minutes = (count: number) => new Date(NOW + count * 60_000).toISOString();

function order(status: OrderStatus, overrides: Partial<Order> = {}): Order {
  return {
    id: crypto.randomUUID(),
    timezone: "America/Los_Angeles",
    status,
    status_changed_at: minutes(0),
    channel: "storefront",
    order_number: 1,
    placed_at: minutes(-5),
    promised_pickup_at: minutes(15),
    customer_name: "Sam",
    customer_phone: "+14155552671",
    lines: [],
    total_cents: 434,
    ...overrides,
  };
}

describe("nextStep", () => {
  it("walks an order through the kitchen", () => {
    expect(nextStep("placed")?.action).toBe("accept");
    expect(nextStep("accepted")?.action).toBe("prepare");
    expect(nextStep("preparing")?.action).toBe("ready");
    expect(nextStep("ready")?.action).toBe("picked_up");
  });

  it("has nothing for a finished order", () => {
    expect(nextStep("picked_up")).toBeNull();
    expect(nextStep("rejected")).toBeNull();
  });
});

describe("wayOut", () => {
  it("is a reject for a new order and a cancel once accepted", () => {
    expect(wayOut("placed")).toBe("reject");
    expect(wayOut("accepted")).toBe("cancel");
  });

  it("closes when cooking starts", () => {
    expect(wayOut("preparing")).toBeNull();
    expect(wayOut("ready")).toBeNull();
  });
});

describe("noShowOffered", () => {
  it("is offered 30 minutes after the order became ready", () => {
    const at = (ago: number) => order("ready", { status_changed_at: minutes(-ago) });

    expect(noShowOffered(at(29), NOW)).toBe(false);
    expect(noShowOffered(at(30), NOW)).toBe(true);
  });

  it("is never offered before the order is ready", () => {
    const old = order("preparing", { status_changed_at: minutes(-90) });

    expect(noShowOffered(old, NOW)).toBe(false);
  });
});

describe("pickupTimer", () => {
  const due = (inMinutes: number, status: OrderStatus = "accepted") =>
    pickupTimer(order(status, { promised_pickup_at: minutes(inMinutes) }), NOW);

  it("counts down to the promised time", () => {
    expect(due(15)).toEqual({ label: "in 15 min", tone: "ok" });
    expect(due(5)).toEqual({ label: "in 5 min", tone: "soon" });
    expect(due(90)).toEqual({ label: "in 1 h 30 min", tone: "ok" });
    expect(due(120)).toEqual({ label: "in 2 h", tone: "ok" });
  });

  it("rounds a part minute up, so the last minute still reads as one", () => {
    expect(due(0.5)).toEqual({ label: "in 1 min", tone: "soon" });
  });

  it("flags an order that is due or late", () => {
    expect(due(0)).toEqual({ label: "due now", tone: "late" });
    expect(due(-7)).toEqual({ label: "7 min late", tone: "late" });
  });

  it("stops counting a ready order as late", () => {
    expect(due(10, "ready")).toEqual({ label: "on time", tone: "ok" });
    expect(due(-12, "ready")).toEqual({ label: "waiting 12 min", tone: "soon" });
  });

  it("is absent without a promised time", () => {
    expect(pickupTimer(order("placed", { promised_pickup_at: null }), NOW)).toBeNull();
  });
});

describe("the board", () => {
  const orders = [order("placed"), order("ready"), order("placed")];

  it("splits orders into columns, keeping their order", () => {
    expect(ordersIn(orders, "placed")).toEqual([orders[0], orders[2]]);
    expect(ordersIn(orders, "preparing")).toEqual([]);
  });

  it("chimes while an order waits to be accepted", () => {
    expect(needsChime(orders)).toBe(true);
    expect(needsChime([order("accepted"), order("ready")])).toBe(false);
    expect(needsChime([])).toBe(false);
  });
});

describe("formatting", () => {
  it("shows times in the restaurant's timezone", () => {
    expect(clockTime("2026-11-18T20:35:00Z", "America/Los_Angeles")).toBe("12:35 PM");
    expect(clockTime("2026-11-18T20:35:00Z", "America/New_York")).toBe("3:35 PM");
  });

  it("shows cents as dollars", () => {
    expect(money(434)).toBe("$4.34");
    expect(money(1200)).toBe("$12.00");
    expect(money(5)).toBe("$0.05");
  });
});
