"""strategies.yaml 的 pydantic 模型、深合并装配与加载函数。

对应 IMPLEMENTATION_SPEC.md §5.1.1/§5.2/§10：策略级的市场差异一律走每条策略的
`market_overrides.<MARKET>` 键，语义 = 对该策略 A股基准参数块的深合并覆盖
（键为 null = 删除该条款）。本模块在 dict 阶段完成合并，再对合并结果做
`model_validate`，为每个 (策略 × 市场) 产出一份 frozen 参数对象。

只在 market_overrides 中出现、CN 基准没有的字段（如 US 专属的
`daily_gain_exclude`/`r_mode`/`crash_day_drop`）在模型中声明为可选、默认 None。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field

from tally.common.config.base import CONFIG_DIR, Market, StrictModel
from tally.common.config.merge import deep_merge

StrategyId = Literal["s1_breakout", "s2_pead", "s4_panic_reversion"]


# ---- S1 趋势突破（IMPLEMENTATION_SPEC.md §5.2）--------------------------------


class S1EntryConfig(StrictModel):
    high_lookback: int = Field(gt=0)
    vol_mult: float = Field(gt=0)
    vol_lookback: int = Field(gt=0)
    ma_gate: int = Field(gt=0)
    bubble_brake_ratio: float = Field(gt=0, le=1)
    limit_up_exclude_ratio: float | None = Field(default=None, gt=0, le=1)
    daily_gain_exclude: float | None = Field(default=None, gt=0)  # 美股专属


class S1ExitConfig(StrictModel):
    chandelier_atr_n: int = Field(gt=0)
    chandelier_atr_mult: float = Field(gt=0)
    ma_break_n: int = Field(gt=0)
    ma_break_days: int = Field(gt=0)
    hard_stop: float = Field(lt=0)


class S1RankingConfig(StrictModel):
    mom_skip: int = Field(ge=0)
    mom_lookback: int = Field(gt=0)


class S1Params(StrictModel):
    enabled: bool
    entry: S1EntryConfig
    exit: S1ExitConfig
    ranking: S1RankingConfig


# ---- S2 事件PEAD（IMPLEMENTATION_SPEC.md §5.2）--------------------------------


class S2EntryConfig(StrictModel):
    r_jump_min: float = Field(gt=0)
    r_vol_mult: float = Field(gt=0)
    confirm_window: int = Field(gt=0)
    roe_ttm_min: float = Field(ge=0)
    ma_gate: int = Field(gt=0)
    r_mode: str | None = None  # 美股专属：price_inferred_2d


class S2ExitConfig(StrictModel):
    time_days: int = Field(gt=0)
    hard_stop: float = Field(lt=0)
    pre_report_min_profit: float = Field(ge=0)


class S2Params(StrictModel):
    enabled: bool
    entry: S2EntryConfig
    exit: S2ExitConfig


# ---- S4 恐慌错杀回补（IMPLEMENTATION_SPEC.md §5.2）-----------------------------


class S4EntryConfig(StrictModel):
    rsi_n: int = Field(gt=0)
    rsi_oversold: float = Field(gt=0, lt=100)
    drop_5d: float = Field(lt=0)
    ma_trend: int = Field(gt=0)
    ma_trend_rising_n: int = Field(gt=0)
    ma_short: int = Field(gt=0)
    vol_spike_max: float = Field(gt=0)
    event_avoid_days: int = Field(gt=0)
    event_avoid_days_fallback: int = Field(gt=0)
    crash_day_drop: float | None = Field(default=None, lt=0)  # 美股专属


class S4ExitConfig(StrictModel):
    rsi_exit: float = Field(gt=0, lt=100)
    time_days: int = Field(gt=0)
    time_days_bear: int = Field(gt=0)
    hard_stop: float = Field(lt=0)


class S4Params(StrictModel):
    enabled: bool
    entry: S4EntryConfig
    exit: S4ExitConfig


StrategyParams = S1Params | S2Params | S4Params

_MODEL_BY_STRATEGY: dict[StrategyId, type[StrategyParams]] = {
    "s1_breakout": S1Params,
    "s2_pead": S2Params,
    "s4_panic_reversion": S4Params,
}

_MARKETS: tuple[Market, ...] = ("CN", "US")

ResolvedStrategies = dict[StrategyId, dict[Market, StrategyParams]]


def _resolve_strategy(
    strategy_id: StrategyId, raw_strategy: dict[str, Any]
) -> dict[Market, StrategyParams]:
    """对单条策略做 market_overrides 深合并装配，返回每个市场的 frozen 参数对象。"""
    overrides: dict[str, dict[str, Any]] = raw_strategy.get("market_overrides", {}) or {}
    base = {k: v for k, v in raw_strategy.items() if k != "market_overrides"}
    model = _MODEL_BY_STRATEGY[strategy_id]

    resolved: dict[Market, StrategyParams] = {}
    for market in _MARKETS:
        merged = deep_merge(base, overrides.get(market, {}))
        resolved[market] = model.model_validate(merged)
    return resolved


def load_strategies_config(path: Path | None = None) -> ResolvedStrategies:
    """加载 config/strategies.yaml，对每条策略执行 market_overrides 深合并并校验。

    返回 {strategy_id: {market: frozen_params}}，每个 (策略×市场) 一份独立冻结实例。
    """
    yaml_path = path or (CONFIG_DIR / "strategies.yaml")
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    unknown = raw.keys() - _MODEL_BY_STRATEGY.keys()
    if unknown:
        raise ValueError(f"strategies.yaml 含未知策略键：{sorted(unknown)}")
    return {
        strategy_id: _resolve_strategy(strategy_id, raw_strategy)
        for strategy_id, raw_strategy in raw.items()
    }
