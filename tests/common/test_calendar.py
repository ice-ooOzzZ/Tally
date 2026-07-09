"""T0.5.4 单测：2019-2026 任意日期 is_trading_day/next/prev + 按 market 分派。"""

from datetime import date

import pytest

from tally.common.calendar import is_trading_day, next_trading_day, prev_trading_day

# ---- CN (SSE) ------------------------------------------------------------------


@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2019, 1, 2), True),  # 2019 首个交易日
        (date(2024, 1, 2), True),  # 元旦后首个交易日
        (date(2024, 2, 12), False),  # 春节休市
        (date(2025, 1, 1), False),  # 元旦
        (date(2026, 1, 1), False),  # 元旦
        (date(2023, 10, 1), False),  # 国庆
        (date(2019, 6, 15), False),  # 周六
        (date(2019, 6, 16), False),  # 周日
    ],
)
def test_cn_is_trading_day(day: date, expected: bool) -> None:
    assert is_trading_day(day, "CN") is expected


def test_cn_next_trading_day_from_trading_day_skips_to_next() -> None:
    # 2024-01-02（周二，交易日）之后严格意义的下一交易日是 2024-01-03。
    assert next_trading_day(date(2024, 1, 2), "CN") == date(2024, 1, 3)


def test_cn_next_trading_day_from_holiday_lands_on_reopen() -> None:
    # 2024 春节休市，节后首个交易日是 2024-02-19。
    assert next_trading_day(date(2024, 2, 12), "CN") == date(2024, 2, 19)


def test_cn_prev_trading_day_from_trading_day_skips_back() -> None:
    assert prev_trading_day(date(2024, 1, 3), "CN") == date(2024, 1, 2)


def test_cn_prev_trading_day_from_holiday_lands_before_close() -> None:
    # 2024-02-12（春节休市第一天）之前上一交易日是节前最后一个交易日 2024-02-08。
    assert prev_trading_day(date(2024, 2, 12), "CN") == date(2024, 2, 8)


def test_cn_next_prev_are_inverse_around_ordinary_day() -> None:
    day = date(2022, 5, 10)
    nxt = next_trading_day(day, "CN")
    assert prev_trading_day(nxt, "CN") == day


# ---- US (NYSE) -----------------------------------------------------------------


@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2019, 1, 2), True),
        (date(2024, 1, 1), False),  # New Year's Day
        (date(2025, 12, 25), False),  # Christmas
        (date(2026, 1, 1), False),  # New Year's Day
        (date(2019, 6, 15), False),  # Saturday
        (date(2020, 11, 26), False),  # Thanksgiving Day
        (date(2020, 11, 27), True),  # 感恩节次日(早收盘), 仍是交易日
    ],
)
def test_us_is_trading_day(day: date, expected: bool) -> None:
    assert is_trading_day(day, "US") is expected


def test_us_next_trading_day() -> None:
    assert next_trading_day(date(2024, 1, 1), "US") == date(2024, 1, 2)


def test_us_prev_trading_day() -> None:
    assert prev_trading_day(date(2024, 1, 1), "US") == date(2023, 12, 29)


# ---- 市场分派：同一天 CN/US 的交易日状态可以不同 ----------------------------------


def test_market_dispatch_differs_between_cn_and_us() -> None:
    # 2020-11-26 感恩节：美股休市，A股照常交易。
    day = date(2020, 11, 26)
    assert is_trading_day(day, "US") is False
    assert is_trading_day(day, "CN") is True


def test_invalid_market_raises() -> None:
    with pytest.raises(ValueError):
        is_trading_day(date(2024, 1, 2), "JP")  # type: ignore[arg-type]
