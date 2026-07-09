"""watchlist.yaml 的 pydantic 模型与加载函数（IMPLEMENTATION_SPEC.md §11 T1.3）。

一期股票池的自动筛选（§6 入池标准）留给 M2 T2.x；T1.3 先用人工维护的固定名单
跑通"名单 → 日K+估值最小同步"这条最小链路。`codes` 元素为 Tushare `ts_code`
格式（带交易所后缀，如 `600000.SH`），与 `TushareSource`/现有 fixture 的调用
惯例一致——落库前才由 `data/sync.py` 剥离后缀对齐 Repository 的 `code` 列。

`start_date` = 该名单首次同步（Repository 内尚无该票历史数据）时的起始日；
增量同步不使用此字段（起点改为"Repository 已有数据的下一交易日"）。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import Field, field_validator

from tally.common.config.base import CONFIG_DIR, Market, StrictModel


class WatchlistConfig(StrictModel):
    """watchlist.yaml 顶层模型。"""

    market: Market
    start_date: date
    codes: tuple[str, ...] = Field(min_length=1)

    @field_validator("codes")
    @classmethod
    def _no_duplicate_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        counts: dict[str, int] = {}
        for code in value:
            counts[code] = counts.get(code, 0) + 1
        duplicates = sorted(code for code, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"codes 存在重复：{duplicates}")
        return value


def load_watchlist_config(path: Path | None = None) -> WatchlistConfig:
    """加载并校验 config/watchlist.yaml。"""
    yaml_path = path or (CONFIG_DIR / "watchlist.yaml")
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return WatchlistConfig.model_validate(raw)
