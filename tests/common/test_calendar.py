"""T0.5.4 单测：2019-2026 任意日期 is_trading_day/next/prev + 按 market 分派。"""

from datetime import date

import pytest

from tally.common.calendar import (
    is_trading_day,
    next_trading_day,
    prev_trading_day,
    trading_days_in_range,
)

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


# ---- 超出日历预加载窗口(2015-2035)的越界错误路径 ---------------------------------
#
# 越界日期取 pandas.Timestamp 可表示范围内、但明显在 2015-2035 窗口之外的值
# （pandas Timestamp 底层是 int64 纳秒计数，可表示范围约 1677-2262 年；
# date(1,1,1)/date(9999,12,31) 会在到达我们的越界检查之前就先在
# `pd.Timestamp(day)` 这一步触发 OutOfBoundsDatetime，测不到我们想测的路径）。


def test_next_trading_day_beyond_preloaded_window_raises() -> None:
    far_future = date(2100, 1, 1)
    with pytest.raises(ValueError, match="之后无已加载交易日"):
        next_trading_day(far_future, "CN")


def test_prev_trading_day_before_preloaded_window_raises() -> None:
    far_past = date(1900, 1, 1)
    with pytest.raises(ValueError, match="之前无已加载交易日"):
        prev_trading_day(far_past, "CN")


def test_next_trading_day_beyond_preloaded_window_raises_for_us_too() -> None:
    far_future = date(2100, 1, 1)
    with pytest.raises(ValueError, match="之后无已加载交易日"):
        next_trading_day(far_future, "US")


# ---- trading_days_in_range（M1 审查整改：补直接单测） -----------------------------------


def test_trading_days_in_range_start_after_end_returns_empty_list() -> None:
    """`start > end`（如 sync 管道判定"该票已无缺失区间"时）返回空列表，非报错。"""
    assert trading_days_in_range(date(2024, 1, 5), date(2024, 1, 2), "CN") == []


def test_trading_days_in_range_inclusive_closed_interval() -> None:
    days = trading_days_in_range(date(2024, 1, 2), date(2024, 1, 4), "CN")
    assert days == [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]


def test_trading_days_in_range_start_equals_end_on_trading_day_returns_single_day() -> None:
    assert trading_days_in_range(date(2024, 1, 2), date(2024, 1, 2), "CN") == [date(2024, 1, 2)]


def test_trading_days_in_range_entirely_within_holiday_returns_empty_list() -> None:
    """区间完全落在假期内（2024 春节休市 02-12~02-18）：无交易日，返回空列表。"""
    assert trading_days_in_range(date(2024, 2, 13), date(2024, 2, 16), "CN") == []


def test_trading_days_in_range_non_trading_day_boundaries_clip_to_inner_days() -> None:
    """start/end 本身是非交易日（周末）：区间应正确圈住区间内部的交易日边界，
    而非因为端点不是交易日就报错或漏算。"""
    # 2024-01-06(周六)~2024-01-08(周一)：区间内仅 01-08 是交易日。
    assert trading_days_in_range(date(2024, 1, 6), date(2024, 1, 8), "CN") == [date(2024, 1, 8)]
    # 2024-01-05(周五)~2024-01-07(周日)：区间内仅 01-05 是交易日。
    assert trading_days_in_range(date(2024, 1, 5), date(2024, 1, 7), "CN") == [date(2024, 1, 5)]
