"""Pickup-time rules that need no database: opening hours and the quote.

Every datetime here is timezone-aware. Times are compared and added in UTC;
the restaurant's timezone only turns its opening hours into instants.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.menu.models import Menu
from app.orders.errors import PickupUnavailable
from app.orders.models import Order

ROUND_TO_MINUTES = 5
# The kitchen's capacity is counted in orders per this many minutes.
CAPACITY_WINDOW_MINUTES = 10
# How far ahead to look for the next opening.
LOOKAHEAD_DAYS = 8


@dataclass(frozen=True)
class OpenWindow:
    """One row of opening hours, in the restaurant's local time."""

    weekday: int  # the day it opens; 0 = Monday
    opens_at: time
    closes_at: time  # <= opens_at means it closes on the next day


@dataclass(frozen=True)
class PickupRules:
    timezone: ZoneInfo
    windows: Sequence[OpenWindow]
    kitchen_capacity: int  # orders per 10 minutes
    busy_extra_minutes: int
    last_order_minutes: int
    accepting_orders: bool


@dataclass(frozen=True)
class OpenPeriod:
    """A stretch of time the restaurant is open."""

    opens: datetime
    closes: datetime
    # The local date it opened on; orders placed in it count for that day.
    business_date: date


@dataclass(frozen=True)
class PickupQuote:
    ready_at: datetime
    business_date: date


def open_period(rules: PickupRules, now: datetime) -> OpenPeriod | None:
    """The period `now` falls in, or None when the restaurant is closed.

    Windows that follow each other without a gap count as one period, so its
    close is when the restaurant really shuts.
    """
    periods = _periods(rules, now)
    current = None
    for period in periods:
        if period.opens <= now < period.closes:
            current = period  # the latest one to open wins
    if current is None:
        return None

    closes = current.closes
    for period in periods:
        if period.opens <= closes < period.closes:
            closes = period.closes
    return OpenPeriod(current.opens, closes, current.business_date)


def next_opening(rules: PickupRules, at: datetime) -> datetime | None:
    """The first time the restaurant opens at or after `at`, if it ever does."""
    for period in _periods(rules, at):
        if period.opens >= at:
            return period.opens
    return None


def wait_minutes(rules: PickupRules, open_orders: int) -> int:
    """Minutes an order waits before the kitchen starts on it.

    The queue delay is the open orders ahead divided by the kitchen's
    capacity, times 10 minutes, plus the busy-mode extra.
    """
    queue = -(-open_orders * CAPACITY_WINDOW_MINUTES // rules.kitchen_capacity)
    return queue + rules.busy_extra_minutes


def longest_prep_minutes(order: Order, menu: Menu | None) -> int:
    """The longest prep time among the order's lines, from the current menu."""
    if menu is None:
        return 0
    items = (menu.find_item(line.menu_item_id) for line in order.lines)
    return max((item.prep_minutes for item in items if item is not None), default=0)


def quote_pickup(
    rules: PickupRules,
    now: datetime,
    requested_at: datetime | None,
    prep_minutes: int,
    open_orders: int,
) -> PickupQuote:
    """When an order placed now would be ready.

    `requested_at` None means as soon as possible. Raises PickupUnavailable
    when no order can be taken now, or the requested time cannot be met.
    """
    if not rules.accepting_orders:
        raise PickupUnavailable(
            "ordering_paused", "The kitchen is not taking orders right now"
        )

    period = open_period(rules, now)
    if period is None:
        raise PickupUnavailable(
            "restaurant_closed",
            "The restaurant is closed right now",
            next_opening_at=next_opening(rules, now),
        )
    reopens_at = next_opening(rules, period.closes)
    if now > period.closes - timedelta(minutes=rules.last_order_minutes):
        raise PickupUnavailable(
            "past_last_order",
            "It is too close to closing time to take an order",
            next_opening_at=reopens_at,
        )

    wait = prep_minutes + wait_minutes(rules, open_orders)
    earliest = _round_up(now + timedelta(minutes=wait))
    if earliest > period.closes:
        raise PickupUnavailable(
            "pickup_too_late",
            "The kitchen cannot have this ready before closing",
            next_opening_at=reopens_at,
        )
    if requested_at is None:
        return PickupQuote(earliest, period.business_date)

    if requested_at < earliest:
        raise PickupUnavailable(
            "pickup_too_early",
            "The order cannot be ready that early",
            earliest_at=earliest,
        )
    ready_at = _round_up(requested_at)
    if ready_at > period.closes:
        raise PickupUnavailable(
            "pickup_too_late",
            "That is after closing time",
            earliest_at=earliest,
        )
    return PickupQuote(ready_at, period.business_date)


def _periods(rules: PickupRules, around: datetime) -> list[OpenPeriod]:
    """Open periods from the day before `around` onwards, earliest first."""
    today = around.astimezone(rules.timezone).date()
    periods = []
    for offset in range(-1, LOOKAHEAD_DAYS):
        day = today + timedelta(days=offset)
        for window in rules.windows:
            if window.weekday != day.weekday():
                continue
            overnight = window.closes_at <= window.opens_at
            close_day = day + timedelta(days=1) if overnight else day
            periods.append(
                OpenPeriod(
                    _instant(day, window.opens_at, rules.timezone),
                    _instant(close_day, window.closes_at, rules.timezone),
                    day,
                )
            )
    return sorted(periods, key=lambda period: period.opens)


def _instant(day: date, at: time, timezone: ZoneInfo) -> datetime:
    return datetime.combine(day, at, tzinfo=timezone).astimezone(UTC)


def _round_up(moment: datetime) -> datetime:
    """The next 5-minute mark at or after `moment`, in UTC."""
    moment = moment.astimezone(UTC)
    floor = moment.replace(
        minute=moment.minute - moment.minute % ROUND_TO_MINUTES,
        second=0,
        microsecond=0,
    )
    return floor if floor == moment else floor + timedelta(minutes=ROUND_TO_MINUTES)
