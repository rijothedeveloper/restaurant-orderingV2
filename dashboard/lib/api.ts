// Client for the staff routes of the ordering API (app/api/staff.py).

export type OrderStatus =
  | "draft"
  | "placed"
  | "accepted"
  | "preparing"
  | "ready"
  | "picked_up"
  | "rejected"
  | "cancelled"
  | "no_show";

export type StaffAction =
  | "accept"
  | "prepare"
  | "ready"
  | "picked_up"
  | "reject"
  | "cancel"
  | "no_show";

export interface OrderLineModifier {
  option_id: string;
  group_name: string;
  name: string;
  kind: "choice" | "add" | "remove";
  price_delta_cents: number;
}

export interface OrderLine {
  id: string;
  name: string;
  quantity: number;
  line_total_cents: number;
  notes: string | null;
  modifiers: OrderLineModifier[];
}

export interface Order {
  id: string;
  timezone: string;
  status: OrderStatus;
  status_changed_at: string;
  channel: string;
  order_number: number | null;
  placed_at: string | null;
  promised_pickup_at: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  lines: OrderLine[];
  total_cents: number;
}

export interface Controls {
  accepting_orders: boolean;
  busy_extra_minutes: number;
}

export interface Board {
  orders: Order[];
  controls: Controls;
}

export interface StaffUser {
  id: string;
  name: string;
}

export interface Session {
  token: string;
  expires_at: string;
  staff: StaffUser;
}

export interface MenuOption {
  id: string;
  name: string;
  is_available: boolean;
}

export interface MenuGroup {
  id: string;
  name: string;
  options: MenuOption[];
}

export interface MenuItem {
  id: string;
  name: string;
  is_available: boolean;
  modifier_groups: MenuGroup[];
}

export interface Menu {
  restaurant_name: string;
  categories: { id: string; name: string; items: MenuItem[] }[];
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    // The order engine's error code, when the API sent one.
    readonly code: string | null,
    message: string,
    // Seconds to wait, from Retry-After.
    readonly retryAfter: number | null,
  ) {
    super(message);
  }
}

async function request<T>(
  path: string,
  options: { method?: string; token?: string; body?: unknown } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.token) headers.Authorization = `Bearer ${options.token}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";

  const response = await fetch(path, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    cache: "no-store",
  });
  if (response.ok) {
    return response.status === 204 ? (undefined as T) : await response.json();
  }

  const body = await response.json().catch(() => null);
  const message =
    typeof body?.message === "string"
      ? body.message
      : typeof body?.detail === "string"
        ? body.detail
        : `Request failed (${response.status})`;
  const retryAfter = Number(response.headers.get("Retry-After"));
  throw new ApiError(
    response.status,
    typeof body?.code === "string" ? body.code : null,
    message,
    Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
  );
}

export function login(restaurantId: string, pin: string): Promise<Session> {
  return request(`/restaurants/${restaurantId}/staff/login`, {
    method: "POST",
    body: { pin },
  });
}

export interface ActionDetails {
  extra_minutes?: 0 | 5 | 10;
  reason?: string;
}

/** The staff routes, for one signed-in session. */
export function staffApi(restaurantId: string, token: string) {
  const base = `/restaurants/${restaurantId}`;
  const staff = `${base}/staff`;
  return {
    logout: () => request<void>(`${staff}/logout`, { method: "POST", token }),
    board: () => request<Board>(`${staff}/orders`, { token }),
    act: (orderId: string, action: StaffAction, details: ActionDetails = {}) =>
      request<Order>(`${staff}/orders/${orderId}/${action}`, {
        method: "POST",
        token,
        body: details,
      }),
    setControls: (change: Partial<Controls>) =>
      request<Controls>(`${staff}/controls`, {
        method: "PATCH",
        token,
        body: change,
      }),
    menu: () => request<Menu>(`${base}/menu`),
    setItemAvailable: (itemId: string, isAvailable: boolean) =>
      request<void>(`${staff}/menu/items/${itemId}`, {
        method: "PATCH",
        token,
        body: { is_available: isAvailable },
      }),
    setOptionAvailable: (optionId: string, isAvailable: boolean) =>
      request<void>(`${staff}/menu/options/${optionId}`, {
        method: "PATCH",
        token,
        body: { is_available: isAvailable },
      }),
  };
}

export type StaffApi = ReturnType<typeof staffApi>;
