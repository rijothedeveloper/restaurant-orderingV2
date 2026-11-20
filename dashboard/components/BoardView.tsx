"use client";

import { useState } from "react";

import type { ActionDetails, Order, StaffAction } from "@/lib/api";
import {
  channelName,
  clockTime,
  COLUMNS,
  money,
  nextStep,
  noShowOffered,
  ordersIn,
  pickupTimer,
  wayOut,
} from "@/lib/board";

type Act = (order: Order, action: StaffAction, details?: ActionDetails) => void;

export function BoardView({
  orders,
  now,
  onAct,
}: {
  orders: Order[];
  now: number;
  onAct: Act;
}) {
  const [leaving, setLeaving] = useState<Order | null>(null);

  return (
    <main className="board">
      {COLUMNS.map(({ status, title }) => {
        const column = ordersIn(orders, status);
        return (
          <section key={status} className={`column ${status}`}>
            <h2>
              {title} <span className="count">{column.length}</span>
            </h2>
            {column.map((order) => (
              <Ticket
                key={order.id}
                order={order}
                now={now}
                onAct={onAct}
                onLeave={() => setLeaving(order)}
              />
            ))}
          </section>
        );
      })}
      {leaving !== null && (
        <ReasonDialog
          order={leaving}
          onClose={() => setLeaving(null)}
          onConfirm={(reason) => {
            const action = wayOut(leaving.status);
            if (action !== null) onAct(leaving, action, { reason });
            setLeaving(null);
          }}
        />
      )}
    </main>
  );
}

function Ticket({
  order,
  now,
  onAct,
  onLeave,
}: {
  order: Order;
  now: number;
  onAct: Act;
  onLeave: () => void;
}) {
  const step = nextStep(order.status);
  const out = wayOut(order.status);
  const timer = pickupTimer(order, now);

  return (
    <article className="ticket">
      <header>
        <strong className="number">#{order.order_number}</strong>
        <span className="name">{order.customer_name}</span>
        {timer !== null && <span className={`timer ${timer.tone}`}>{timer.label}</span>}
      </header>
      <p className="meta">
        {channelName(order.channel)}
        {order.promised_pickup_at !== null &&
          ` · pickup ${clockTime(order.promised_pickup_at, order.timezone)}`}
        {order.customer_phone !== null && ` · ${order.customer_phone}`}
      </p>
      <ul className="lines">
        {order.lines.map((line) => (
          <li key={line.id}>
            <span className="qty">{line.quantity}×</span> {line.name}
            {line.modifiers.length > 0 && (
              <ul className="modifiers">
                {line.modifiers.map((modifier) => (
                  <li
                    key={modifier.option_id}
                    className={modifier.kind === "remove" ? "remove" : undefined}
                  >
                    {modifier.name}
                  </li>
                ))}
              </ul>
            )}
            {line.notes !== null && <p className="note">Note: {line.notes}</p>}
          </li>
        ))}
      </ul>
      <p className="total">
        {money(order.total_cents)} <span className="unpaid">unpaid</span>
      </p>
      <footer>
        {step !== null && (
          <button className="primary" onClick={() => onAct(order, step.action)}>
            {step.label}
          </button>
        )}
        {order.status === "placed" && (
          <>
            <button onClick={() => onAct(order, "accept", { extra_minutes: 5 })}>
              +5 min
            </button>
            <button onClick={() => onAct(order, "accept", { extra_minutes: 10 })}>
              +10 min
            </button>
          </>
        )}
        {out !== null && (
          <button className="quiet danger" onClick={onLeave}>
            {out === "reject" ? "Reject" : "Cancel"}
          </button>
        )}
        {noShowOffered(order, now) && (
          <button className="quiet danger" onClick={() => onAct(order, "no_show")}>
            No-show
          </button>
        )}
      </footer>
    </article>
  );
}

const REASONS = ["Out of an item", "Too busy right now", "Closing soon", "Customer asked"];

function ReasonDialog({
  order,
  onClose,
  onConfirm,
}: {
  order: Order;
  onClose: () => void;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const verb = wayOut(order.status) === "reject" ? "Reject" : "Cancel";

  return (
    <div className="scrim" onClick={onClose}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label={`${verb} order ${order.order_number}`}
        onClick={(event) => event.stopPropagation()}
      >
        <h2>
          {verb} order #{order.order_number}?
        </h2>
        <p>Choose or type the reason.</p>
        <div className="reasons">
          {REASONS.map((preset) => (
            <button
              key={preset}
              className={preset === reason ? "active" : ""}
              onClick={() => setReason(preset)}
            >
              {preset}
            </button>
          ))}
        </div>
        <input
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          maxLength={200}
          placeholder="Reason"
          aria-label="Reason"
        />
        <footer>
          <button className="quiet" onClick={onClose}>
            Keep order
          </button>
          <button
            className="primary danger"
            disabled={reason.trim() === ""}
            onClick={() => onConfirm(reason.trim())}
          >
            {verb} order
          </button>
        </footer>
      </div>
    </div>
  );
}
