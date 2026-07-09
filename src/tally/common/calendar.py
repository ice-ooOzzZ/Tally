"""交易日历（IMPLEMENTATION_SPEC.md §3.1 #11/#18，T0.5.4 骨架实现）。

接口按 market 分派：CN 后续接 Tushare `trade_cal` 落库（更精确、含临时调休），
当前脚手架阶段 **CN 与 US 都用 pandas_market_calendars 占位**
（CN="SSE"，US="NYSE"，日历名来自 MarketProfile.calendar_name）。

语义：
- `next_trading_day` / `prev_trading_day` 均为**严格**意义上的下一/上一交易日
  （即使传入日期本身是交易日，也会跳到相邻的另一个交易日），对齐 §4.4 的
  "T+1 = 次一交易时段"表述。
"""

from __future__ import annotations

from datetime import date
from functools import cache

import pandas as pd
import pandas_market_calendars as mcal

from tally.common.market_profile import Market, get_market_profile

# 日历预加载范围：覆盖 spec 要求的 2019-2026 验证区间并留出前后缓冲。
_SCHEDULE_START = "2015-01-01"
_SCHEDULE_END = "2035-12-31"


@cache
def _trading_days(market: Market) -> pd.DatetimeIndex:
    """按市场取交易日索引（升序、已归一到日期，无时区/时间部分），带缓存。"""
    calendar_name = get_market_profile(market).calendar_name
    calendar = mcal.get_calendar(calendar_name)
    schedule = calendar.schedule(start_date=_SCHEDULE_START, end_date=_SCHEDULE_END)
    return pd.DatetimeIndex(schedule.index.normalize())


def is_trading_day(day: date, market: Market) -> bool:
    """`day` 是否为 `market` 的交易日。"""
    return pd.Timestamp(day) in _trading_days(market)


def next_trading_day(day: date, market: Market) -> date:
    """`market` 严格晚于 `day` 的下一个交易日。"""
    days = _trading_days(market)
    pos = int(days.searchsorted(pd.Timestamp(day), side="right"))
    if pos >= len(days):
        raise ValueError(f"{day} 之后无已加载交易日（market={market}），需扩大日历预加载范围")
    return days[pos].date()


def prev_trading_day(day: date, market: Market) -> date:
    """`market` 严格早于 `day` 的上一个交易日。"""
    days = _trading_days(market)
    pos = int(days.searchsorted(pd.Timestamp(day), side="left")) - 1
    if pos < 0:
        raise ValueError(f"{day} 之前无已加载交易日（market={market}），需扩大日历预加载范围")
    return days[pos].date()


def trading_days_in_range(start: date, end: date, market: Market) -> list[date]:
    """`market` 在 `[start, end]` 闭区间内的交易日列表（升序）。

    `start > end`（如 T1.3 同步管道判定"该票已无缺失区间"时）直接返回空列表，
    而非报错——调用方（`data/sync.py`）据此判断该票本次无需拉取，是正常路径
    而非异常路径。
    """
    if start > end:
        return []
    days = _trading_days(market)
    mask = (days >= pd.Timestamp(start)) & (days <= pd.Timestamp(end))
    return [ts.date() for ts in days[mask]]
