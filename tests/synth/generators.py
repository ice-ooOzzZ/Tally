"""合成K线生成器（IMPLEMENTATION_SPEC.md §11 T0.5.5）。

六种形态：uptrend / downtrend / sideways / breakout / crash / recover。
支持参数化：
- `extreme_moves`：在指定交易日强制注入某个单日涨跌幅（用于测试极端行情下游逻辑）；
- `apply_price_limit`：是否施加涨跌停约束（True=A股模式，按 `limit_pct` 双向裁剪单日
  收益；False=美股模式，无约束，注入的极端涨跌幅原样保留）；
- `event_day_indices`：伴随事件日标注序列（写入返回 DataFrame 的 `is_event_day` 列，
  并对当日成交量做固定倍数放大，模拟财报/公告日放量）。

固定 `seed` 时输出逐字节可复现（纯函数：不修改传入的 SynthConfig，也不依赖任何全局状态）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

import numpy as np
import pandas as pd

Shape = Literal["uptrend", "downtrend", "sideways", "breakout", "crash", "recover"]

# 事件日成交量放大倍数（合成数据用固定常量，非生产参数，故不放 config/*.yaml）。
_EVENT_DAY_VOLUME_MULT = 1.8
_BASE_VOLUME = 1_000_000.0
_VOLUME_NOISE_SIGMA = 0.15
_INTRADAY_RANGE_SIGMA = 0.006


@dataclass(frozen=True)
class ExtremeMove:
    """在第 `day_index`（0-based）个交易日强制注入 `pct_change` 的单日收盘涨跌幅。"""

    day_index: int
    pct_change: float


@dataclass(frozen=True)
class SynthConfig:
    """合成K线生成参数。"""

    shape: Shape
    n_days: int = 250
    start_price: float = 100.0
    seed: int = 42
    start_date: date = date(2024, 1, 2)
    apply_price_limit: bool = True
    limit_pct: float = 0.10
    extreme_moves: tuple[ExtremeMove, ...] = field(default_factory=tuple)
    event_day_indices: tuple[int, ...] = field(default_factory=tuple)


def _shape_returns(shape: Shape, rng: np.random.Generator, n: int) -> np.ndarray:
    """按形态生成 n 个单日收益率（不含极端注入/涨跌停裁剪）。"""
    if shape == "uptrend":
        return rng.normal(loc=0.0015, scale=0.015, size=n)
    if shape == "downtrend":
        return rng.normal(loc=-0.0015, scale=0.015, size=n)
    if shape == "sideways":
        return rng.normal(loc=0.0, scale=0.010, size=n)
    if shape == "breakout":
        split = int(n * 0.7)
        calm = rng.normal(loc=0.0, scale=0.008, size=split)
        jump = np.array([0.06]) if n - split > 0 else np.array([])
        trend = rng.normal(loc=0.006, scale=0.018, size=max(n - split - len(jump), 0))
        return np.concatenate([calm, jump, trend])[:n]
    if shape == "crash":
        # 各段长度依次钳制在剩余可用天数内，避免 n_days 很小时 size 变负
        # （np.random.Generator.normal 对负 size 会抛 ValueError: negative dimensions）。
        calm_len = min(int(n * 0.75), n)
        crash_len = min(max(int(n * 0.06), 3), max(n - calm_len, 0))
        tail_len = max(n - calm_len - crash_len, 0)
        calm = rng.normal(loc=0.0005, scale=0.012, size=calm_len)
        crash = rng.normal(loc=-0.05, scale=0.02, size=crash_len)
        tail = rng.normal(loc=-0.001, scale=0.015, size=tail_len)
        return np.concatenate([calm, crash, tail])[:n]
    if shape == "recover":
        crash_len = min(max(int(n * 0.06), 3), n)
        rebound_len = max(n - crash_len, 0)
        crash = rng.normal(loc=-0.05, scale=0.02, size=crash_len)
        rebound = rng.normal(loc=0.010, scale=0.018, size=rebound_len)
        return np.concatenate([crash, rebound])[:n]
    raise ValueError(f"未知形态 {shape!r}")


def _apply_extreme_moves(returns: np.ndarray, moves: tuple[ExtremeMove, ...]) -> np.ndarray:
    result = returns.copy()
    for move in moves:
        if not 0 <= move.day_index < len(result):
            raise ValueError(f"extreme move day_index={move.day_index} 超出 n_days={len(result)}")
        result[move.day_index] = move.pct_change
    return result


def _apply_price_limit(returns: np.ndarray, limit_pct: float) -> np.ndarray:
    return np.clip(returns, -limit_pct, limit_pct)


def _business_dates(start: date, n: int) -> pd.DatetimeIndex:
    """从 start 起生成 n 个连续工作日（合成数据用途，不接交易日历，避免与 calendar.py 耦合）。"""
    dates: list[date] = []
    current = start
    while len(dates) < n:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return pd.DatetimeIndex(dates)


def generate_synthetic_kline(config: SynthConfig) -> pd.DataFrame:
    """按 `config` 生成一条合成K线；相同 config 输出逐字节可复现。"""
    rng = np.random.default_rng(config.seed)
    returns = _shape_returns(config.shape, rng, config.n_days)
    returns = _apply_extreme_moves(returns, config.extreme_moves)
    if config.apply_price_limit:
        returns = _apply_price_limit(returns, config.limit_pct)

    close = config.start_price * np.cumprod(1.0 + returns)
    prev_close = np.concatenate([[config.start_price], close[:-1]])

    gap_noise = rng.normal(loc=0.0, scale=_INTRADAY_RANGE_SIGMA / 2, size=config.n_days)
    open_ = prev_close * (1.0 + gap_noise)

    range_noise = np.abs(rng.normal(loc=0.0, scale=_INTRADAY_RANGE_SIGMA, size=config.n_days))
    high = np.maximum(open_, close) * (1.0 + range_noise)
    low = np.minimum(open_, close) * (1.0 - range_noise)

    volume_noise = rng.normal(loc=0.0, scale=_VOLUME_NOISE_SIGMA, size=config.n_days)
    volume = _BASE_VOLUME * (1.0 + volume_noise)
    volume = np.clip(volume, a_min=_BASE_VOLUME * 0.1, a_max=None)

    is_event_day = np.zeros(config.n_days, dtype=bool)
    for idx in config.event_day_indices:
        if not 0 <= idx < config.n_days:
            raise ValueError(f"event_day_index={idx} 超出 n_days={config.n_days}")
        is_event_day[idx] = True
    volume = np.where(is_event_day, volume * _EVENT_DAY_VOLUME_MULT, volume)

    dates = _business_dates(config.start_date, config.n_days)

    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "is_event_day": is_event_day,
        }
    )
