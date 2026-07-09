"""backtest.yaml 的 pydantic 模型与加载函数（IMPLEMENTATION_SPEC.md §7/§10）。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import Field

from tally.common.config.base import CONFIG_DIR, StrictModel


class TrainPeriodConfig(StrictModel):
    start: date
    end: date


class OosPeriodConfig(StrictModel):
    start: date
    view_budget: int = Field(gt=0)


class StampDutyTierConfig(StrictModel):
    """印花税分段（spec §7：2023-08-28 前后费率不同）。until/from_ 至少一个非空。"""

    until: date | None = None
    from_: date | None = Field(default=None, alias="from")
    rate: float = Field(ge=0, le=1)


class SlippageConfig(StrictModel):
    s1: float = Field(ge=0, le=1)
    s2: float = Field(ge=0, le=1)
    s4: float = Field(ge=0, le=1)


class CNCostsConfig(StrictModel):
    commission: float = Field(ge=0, le=1)
    commission_min: float = Field(ge=0)
    stamp_duty: list[StampDutyTierConfig] = Field(min_length=1)
    slippage: SlippageConfig
    dividend_tax_lt1m: float = Field(ge=0, le=1)


class CapitalGainsTaxConfig(StrictModel):
    enabled: bool
    rate: float = Field(ge=0, le=1)


class USCostsConfig(StrictModel):
    commission_per_share: float = Field(ge=0)
    commission_min: float = Field(ge=0)
    slippage: SlippageConfig
    slippage_realistic: float = Field(ge=0, le=1)
    dividend_withholding: float = Field(ge=0, le=1)
    capital_gains_tax: CapitalGainsTaxConfig


class CostsConfig(StrictModel):
    CN: CNCostsConfig
    US: USCostsConfig


class BacktestConfig(StrictModel):
    """backtest.yaml 顶层模型。"""

    train: TrainPeriodConfig
    oos: OosPeriodConfig
    costs: CostsConfig


def load_backtest_config(path: Path | None = None) -> BacktestConfig:
    """加载并校验 config/backtest.yaml。"""
    yaml_path = path or (CONFIG_DIR / "backtest.yaml")
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return BacktestConfig.model_validate(raw)
