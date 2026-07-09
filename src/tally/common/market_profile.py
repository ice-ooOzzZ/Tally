"""MarketProfile：市场差异注入机制（IMPLEMENTATION_SPEC.md §5.1.1）。

运行时只读（frozen dataclass），由 strategy/registry.py 装配策略时按市场构建，
挂到 MarketContext.profile。策略主逻辑只读 self.params 与 ctx.profile 的能力开关，
禁止出现 `if market ==` 字面判断（本文件与 backtest/broker.py 除外，见 CLAUDE.md）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

Market = Literal["CN", "US"]


@dataclass(frozen=True)
class MarketProfile:
    """单一市场的能力画像；字段含义见 IMPLEMENTATION_SPEC.md §5.1.1。"""

    market: Market
    currency: str
    benchmark: str
    calendar_name: str
    industry_taxonomy: str
    has_price_limit: bool
    t_plus_one: bool
    fin_effective_rule: str
    has_scheduled_disclosure: bool
    event_date_mode: str


# 市场级常量表（代码内维护；策略级差异一律走 strategies.yaml 的 market_overrides，
# 不得放在这里 —— 见 §5.1.1 装配规则）。benchmark/currency 与 system.yaml 保持一致。
_MARKET_PROFILES: Final[dict[Market, MarketProfile]] = {
    "CN": MarketProfile(
        market="CN",
        currency="CNY",
        benchmark="H00300",
        calendar_name="SSE",
        industry_taxonomy="SW_L1",
        has_price_limit=True,
        t_plus_one=True,
        fin_effective_rule="announce_plus_1td",
        has_scheduled_disclosure=True,
        event_date_mode="announced",
    ),
    "US": MarketProfile(
        market="US",
        currency="USD",
        benchmark="^SP500TR",
        calendar_name="NYSE",
        industry_taxonomy="GICS_SECTOR",
        has_price_limit=False,
        t_plus_one=False,
        fin_effective_rule="filed_plus_1td",
        has_scheduled_disclosure=False,
        event_date_mode="price_inferred",
    ),
}


def get_market_profile(market: Market) -> MarketProfile:
    """按市场取 MarketProfile。这是本文件中唯一允许存在的市场字面量分支点。"""
    try:
        return _MARKET_PROFILES[market]
    except KeyError as exc:
        valid = ", ".join(_MARKET_PROFILES)
        raise ValueError(f"未知市场 {market!r}，可选：{valid}") from exc
