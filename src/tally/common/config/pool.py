"""pool.yaml 的 pydantic 模型与加载函数（IMPLEMENTATION_SPEC.md §6/§10）。

CN 与 US 的入池标准字段集不同（A股用市值/PE/PB/成交额，美股用市值(USD)/PE/ROE/成交量），
因此各自建模，而非共用一个 EntryConfig。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field

from tally.common.config.base import CONFIG_DIR, StrictModel


class CNPoolEntryConfig(StrictModel):
    min_mcap: float = Field(gt=0)
    pe_ttm_range: tuple[float, float]
    pb_range: tuple[float, float]
    min_amount_20d: float = Field(gt=0)
    min_list_days: int = Field(gt=0)
    exclude_st: bool


class USPoolEntryConfig(StrictModel):
    min_mcap_usd: float = Field(gt=0)
    pe_range: tuple[float, float]
    min_roe: float
    min_avg_volume: float = Field(gt=0)
    roe_skip_if_missing: bool


class CNPoolConfig(StrictModel):
    entry: CNPoolEntryConfig
    size: int = Field(gt=0)
    industry_cap_count: float = Field(gt=0, le=1)
    exit_grace_weeks: int = Field(gt=0)


class USPoolConfig(StrictModel):
    universe: str = Field(min_length=1)
    entry: USPoolEntryConfig
    size: int = Field(gt=0)
    industry_cap_count: float = Field(gt=0, le=1)
    exit_grace_weeks: int = Field(gt=0)


class PoolConfig(StrictModel):
    """pool.yaml 顶层模型。"""

    CN: CNPoolConfig
    US: USPoolConfig


def load_pool_config(path: Path | None = None) -> PoolConfig:
    """加载并校验 config/pool.yaml。"""
    yaml_path = path or (CONFIG_DIR / "pool.yaml")
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return PoolConfig.model_validate(raw)
