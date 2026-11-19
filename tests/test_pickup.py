from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.orders.errors import PickupUnavailable
from app.orders.pickup import (
    OpenWindow,
    PickupRules,
    longest_prep_minutes,
    next_opening,
    open_period,
    quote_pickup,
    wait_minutes,
)
from tests.test_order_pricing import make_item, make_line, make_menu, make_order

LA = ZoneInfo("America/Los_Angeles")
WEDNESDAY = date(2026, 11, 18)
THURSDAY = date(2026, 11, 19)


def local(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """A Los Angeles wall-clock time as a UTC instant."""
    return datetime.combine(day, time(hour, minute, second), tzinfo=LA).astimezone(UTC)


def daily(opens: time, closes: time) -> list[OpenWindow]:
    return [OpenWindow(weekday, opens, closes) for weekday in range(7)]


def make_rules(windows: list[OpenWindow] | None = None, **overrides) -> PickupRules:
    """Open 09:00 to 21:00 every day unless told otherwise."""
    settings = {
        "kitchen_capacity": 3,
        "busy_extra_minutes": 0,
        "last_order_minutes": 15,
        "accepting_orders": True,
        **overrides,
    }
    if windows is None:
        windows = daily(time(9), time(21))
    return PickupRules(timezone=LA, windows=windows, **settings)


def quote(rules: PickupRules, now: datetime, requested_at=None, prep=10, open_orders=0):
    return quote_pickup(rules, now, requested_at, prep, open_orders)


def rejection(*args, **kwargs) -> PickupUnavailable:
    with pytest.raises(PickupUnavailable) as error:
        quote(*args, **kwargs)
    return error.value


# --- as soon as possible ----------------------------------------------------


def test_asap_is_now_plus_the_longest_prep_time() -> None:
    result = quote(make_rules(), local(WEDNESDAY, 12))

    assert result.ready_at == local(WEDNESDAY, 12, 10)
    assert result.business_date == WEDNESDAY


def test_queue_delay_is_open_orders_over_capacity_times_ten_minutes() -> None:
    # The architecture doc's example: 6 open orders at 3 per 10 minutes add 20.
    result = quote(make_rules(), local(WEDNESDAY, 12), open_orders=6)

    assert result.ready_at == local(WEDNESDAY, 12, 30)


@pytest.mark.parametrize(
    ("open_orders", "capacity", "busy", "expected"),
    [(0, 3, 0, 0), (1, 3, 0, 4), (4, 3, 0, 14), (6, 3, 0, 20), (2, 1, 20, 40)],
)
def test_wait_minutes(open_orders: int, capacity: int, busy: int, expected: int):
    rules = make_rules(kitchen_capacity=capacity, busy_extra_minutes=busy)

    assert wait_minutes(rules, open_orders) == expected


def test_busy_mode_adds_its_extra_minutes() -> None:
    result = quote(make_rules(busy_extra_minutes=20), local(WEDNESDAY, 12))

    assert result.ready_at == local(WEDNESDAY, 12, 30)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (local(WEDNESDAY, 12, 0), local(WEDNESDAY, 12, 0)),
        (local(WEDNESDAY, 12, 0, 1), local(WEDNESDAY, 12, 5)),
        (local(WEDNESDAY, 12, 1), local(WEDNESDAY, 12, 5)),
        (local(WEDNESDAY, 12, 56), local(WEDNESDAY, 13, 0)),
    ],
)
def test_quote_rounds_up_to_five_minutes(now: datetime, expected: datetime) -> None:
    assert quote(make_rules(), now, prep=0).ready_at == expected


# --- a requested time -------------------------------------------------------


def test_a_requested_time_is_kept_and_rounded_up() -> None:
    rules, now = make_rules(), local(WEDNESDAY, 12)

    assert quote(rules, now, local(WEDNESDAY, 13)).ready_at == local(WEDNESDAY, 13)
    assert quote(rules, now, local(WEDNESDAY, 13, 2)).ready_at == local(
        WEDNESDAY, 13, 5
    )
    # The earliest possible time itself is fine.
    assert quote(rules, now, local(WEDNESDAY, 12, 10)).ready_at == local(
        WEDNESDAY, 12, 10
    )


def test_a_requested_time_can_be_in_any_timezone() -> None:
    one_pm_la = datetime(2026, 11, 18, 16, 0, tzinfo=ZoneInfo("America/New_York"))

    result = quote(make_rules(), local(WEDNESDAY, 12), one_pm_la)

    assert result.ready_at == local(WEDNESDAY, 13)


def test_a_requested_time_before_the_earliest_is_rejected_with_the_earliest() -> None:
    error = rejection(make_rules(), local(WEDNESDAY, 12), local(WEDNESDAY, 12, 5))

    assert error.code == "pickup_too_early"
    assert error.earliest_at == local(WEDNESDAY, 12, 10)
    assert error.next_opening_at is None


def test_a_requested_time_after_closing_is_rejected() -> None:
    rules, now = make_rules(), local(WEDNESDAY, 12)

    assert quote(rules, now, local(WEDNESDAY, 21)).ready_at == local(WEDNESDAY, 21)
    for too_late in (local(WEDNESDAY, 21, 1), local(THURSDAY, 12)):
        error = rejection(rules, now, too_late)
        assert error.code == "pickup_too_late"
        assert error.earliest_at == local(WEDNESDAY, 12, 10)


# --- closed, last orders and pause ------------------------------------------


@pytest.mark.parametrize(
    ("now", "next_opening_at"),
    [
        (local(WEDNESDAY, 8, 59), local(WEDNESDAY, 9)),
        (local(WEDNESDAY, 21), local(THURSDAY, 9)),
        (local(WEDNESDAY, 23, 30), local(THURSDAY, 9)),
    ],
)
def test_closed_gives_the_next_opening(now: datetime, next_opening_at: datetime):
    error = rejection(make_rules(), now)

    assert error.code == "restaurant_closed"
    assert error.next_opening_at == next_opening_at
    assert error.earliest_at is None


def test_closed_days_are_skipped_to_the_next_open_day() -> None:
    mondays_only = make_rules([OpenWindow(0, time(9), time(21))])

    error = rejection(mondays_only, local(WEDNESDAY, 12))

    assert error.code == "restaurant_closed"
    assert error.next_opening_at == local(date(2026, 11, 23), 9)


def test_a_restaurant_without_hours_is_closed() -> None:
    error = rejection(make_rules([]), local(WEDNESDAY, 12))

    assert error.code == "restaurant_closed"
    assert error.next_opening_at is None


def test_last_orders_end_before_closing() -> None:
    rules = make_rules(last_order_minutes=15)

    # 15 minutes before closing is still in time.
    assert quote(rules, local(WEDNESDAY, 20, 45)).ready_at == local(WEDNESDAY, 20, 55)
    error = rejection(rules, local(WEDNESDAY, 20, 46))
    assert error.code == "past_last_order"
    assert error.next_opening_at == local(THURSDAY, 9)


def test_an_order_that_cannot_be_ready_by_closing_is_rejected() -> None:
    error = rejection(make_rules(), local(WEDNESDAY, 20, 40), prep=30)

    assert error.code == "pickup_too_late"
    assert error.next_opening_at == local(THURSDAY, 9)
    assert error.earliest_at is None


def test_paused_ordering_rejects_even_while_open() -> None:
    error = rejection(make_rules(accepting_orders=False), local(WEDNESDAY, 12))

    assert error.code == "ordering_paused"
    assert error.next_opening_at is None


# --- opening hours ----------------------------------------------------------


def test_a_window_past_midnight_belongs_to_the_day_it_opened() -> None:
    evenings = make_rules(daily(time(17), time(1)))

    result = quote(evenings, local(THURSDAY, 0, 30))

    assert result.ready_at == local(THURSDAY, 0, 40)
    assert result.business_date == WEDNESDAY
    error = rejection(evenings, local(THURSDAY, 1, 30))
    assert error.code == "restaurant_closed"
    assert error.next_opening_at == local(THURSDAY, 17)


def test_windows_without_a_gap_are_one_period() -> None:
    lunch_then_dinner = make_rules(daily(time(9), time(15)) + daily(time(15), time(21)))

    # Ten minutes before lunch "closes" is not the last order of the day.
    result = quote(lunch_then_dinner, local(WEDNESDAY, 14, 50), prep=15)

    assert result.ready_at == local(WEDNESDAY, 15, 5)
    period = open_period(lunch_then_dinner, local(WEDNESDAY, 14, 50))
    assert period.closes == local(WEDNESDAY, 21)


def test_open_all_day_never_reaches_a_last_order() -> None:
    always = make_rules(daily(time(0), time(0)))

    result = quote(always, local(WEDNESDAY, 23, 55))

    assert result.ready_at == local(THURSDAY, 0, 5)
    assert result.business_date == WEDNESDAY


def test_a_split_day_is_closed_in_between() -> None:
    split = make_rules(daily(time(11), time(14)) + daily(time(17), time(21)))

    assert open_period(split, local(WEDNESDAY, 15)) is None
    assert next_opening(split, local(WEDNESDAY, 15)) == local(WEDNESDAY, 17)
    assert open_period(split, local(WEDNESDAY, 13)).closes == local(WEDNESDAY, 14)


def test_hours_follow_the_wall_clock_across_daylight_saving() -> None:
    rules = make_rules()
    # Clocks go back on 1 November 2026: 09:00 is 17:00 UTC, no longer 16:00.
    fall_back = date(2026, 11, 1)

    assert open_period(rules, datetime(2026, 11, 1, 16, 30, tzinfo=UTC)) is None
    period = open_period(rules, datetime(2026, 11, 1, 17, 0, tzinfo=UTC))
    assert period.opens == datetime(2026, 11, 1, 17, 0, tzinfo=UTC)
    assert period.closes - period.opens == timedelta(hours=12)
    assert period.business_date == fall_back

    # Clocks go forward on 14 March 2027: 09:00 is 16:00 UTC from that day.
    assert next_opening(rules, datetime(2027, 3, 14, 12, 0, tzinfo=UTC)) == datetime(
        2027, 3, 14, 16, 0, tzinfo=UTC
    )


# --- prep time --------------------------------------------------------------


def test_longest_prep_minutes_takes_the_slowest_item_on_the_menu() -> None:
    sandwich = make_item()  # 12 minutes
    fries = make_item().model_copy(update={"prep_minutes": 6})
    gone = make_item().model_copy(update={"prep_minutes": 60})
    menu = make_menu(sandwich, fries)
    order = make_order(make_line(fries), make_line(sandwich), make_line(gone))

    assert longest_prep_minutes(order, menu) == 12
    assert longest_prep_minutes(make_order(), menu) == 0
    assert longest_prep_minutes(order, None) == 0
