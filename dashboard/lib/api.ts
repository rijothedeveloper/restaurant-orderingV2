// Client for the staff and manager routes of the ordering API
// (app/api/staff.py and app/api/admin.py).

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

export type Role = "manager" | "staff";

export interface StaffUser {
  id: string;
  name: string;
  role: Role;
}

/** A staff user as a manager sees them. */
export interface StaffMember extends StaffUser {
  is_active: boolean;
}

export interface StaffChange {
  name?: string;
  role?: Role;
  pin?: string;
  is_active?: boolean;
}

export type OptionKind = "choice" | "add" | "remove";

export interface EditableOption {
  id: string;
  name: string;
  kind: OptionKind;
  price_delta_cents: number;
  is_available: boolean;
}

export interface EditableGroup {
  id: string;
  name: string;
  min_selections: number;
  max_selections: number;
  options: EditableOption[];
}

export interface EditableItem {
  id: string;
  name: string;
  description: string | null;
  price_cents: number;
  prep_minutes: number;
  is_available: boolean;
  aliases: string[];
  group_ids: string[];
}

export interface EditableCategory {
  id: string;
  name: string;
  items: EditableItem[];
}

/** The menu as a manager edits it; every menu change answers with it. */
export interface EditableMenu {
  categories: EditableCategory[];
  groups: EditableGroup[];
}

export interface ItemInput {
  category_id: string;
  name: string;
  description: string | null;
  price_cents: number;
  prep_minutes: number;
  aliases: string[];
  group_ids: string[];
}

export interface GroupInput {
  name: string;
  min_selections: number;
  max_selections: number;
  // In menu order. Options of the group left out are removed; one without
  // an id is new.
  options: { id?: string; name: string; kind: OptionKind; price_delta_cents: number }[];
}

export interface OpenHours {
  weekday: number; // 0 = Monday
  opens_at: string; // local "HH:MM" or "HH:MM:SS"
  closes_at: string; // at or before opens_at: closes the next day
}

export interface KitchenSettings {
  tax_rate_bps: number;
  kitchen_capacity: number;
  last_order_minutes: number;
}

export interface RestaurantSetup {
  restaurant_name: string;
  timezone: string;
  settings: KitchenSettings;
  hours: OpenHours[];
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
  const admin = `${staff}/admin`;
  const send = <T>(method: string, path: string, body?: unknown) =>
    request<T>(path, { method, token, body });
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
    /** Manager only: anyone else gets a 403. */
    admin: {
      staff: () => send<StaffMember[]>("GET", `${admin}/users`),
      addStaff: (member: { name: string; pin: string; role: Role }) =>
        send<StaffMember>("POST", `${admin}/users`, member),
      changeStaff: (staffId: string, change: StaffChange) =>
        send<StaffMember>("PATCH", `${admin}/users/${staffId}`, change),

      menu: () => send<EditableMenu>("GET", `${admin}/menu`),
      addCategory: (name: string) =>
        send<EditableMenu>("POST", `${admin}/menu/categories`, { name }),
      renameCategory: (categoryId: string, name: string) =>
        send<EditableMenu>("PATCH", `${admin}/menu/categories/${categoryId}`, { name }),
      deleteCategory: (categoryId: string) =>
        send<EditableMenu>("DELETE", `${admin}/menu/categories/${categoryId}`),
      orderCategories: (ids: string[]) =>
        send<EditableMenu>("PUT", `${admin}/menu/categories/order`, { ids }),
      orderItems: (categoryId: string, ids: string[]) =>
        send<EditableMenu>("PUT", `${admin}/menu/categories/${categoryId}/items/order`, {
          ids,
        }),
      addItem: (item: ItemInput) =>
        send<EditableMenu>("POST", `${admin}/menu/items`, item),
      changeItem: (itemId: string, item: ItemInput) =>
        send<EditableMenu>("PUT", `${admin}/menu/items/${itemId}`, item),
      deleteItem: (itemId: string) =>
        send<EditableMenu>("DELETE", `${admin}/menu/items/${itemId}`),
      addGroup: (group: GroupInput) =>
        send<EditableMenu>("POST", `${admin}/menu/groups`, group),
      changeGroup: (groupId: string, group: GroupInput) =>
        send<EditableMenu>("PUT", `${admin}/menu/groups/${groupId}`, group),
      deleteGroup: (groupId: string) =>
        send<EditableMenu>("DELETE", `${admin}/menu/groups/${groupId}`),

      setup: () => send<RestaurantSetup>("GET", `${admin}/settings`),
      setSettings: (settings: KitchenSettings) =>
        send<RestaurantSetup>("PUT", `${admin}/settings`, settings),
      setHours: (hours: OpenHours[]) =>
        send<RestaurantSetup>("PUT", `${admin}/hours`, { hours }),
    },
  };
}

export type StaffApi = ReturnType<typeof staffApi>;
