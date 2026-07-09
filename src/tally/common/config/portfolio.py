"""portfolio.yaml 的 pydantic 模型与加载函数（IMPLEMENTATION_SPEC.md §4/§10）。

结构：每市场一套（CN/US，字段相同）+ 顶层 ledger_guard（跨市场共用的账本失真防护阈值）。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field, model_validator

from tally.common.config.base import CONFIG_DIR, StrictModel

# 浮点误差容忍（yaml 里的配额多为两位小数，累加误差远小于此值）。
_QUOTA_SUM_TOLERANCE = 1e-6


class QuotasConfig(StrictModel):
    """三条策略的目标配额（现金底仓 = 1 - s1 - s2 - s4，不单独建模，由下方校验保证≥0）。"""

    s1: float = Field(ge=0, le=1)
    s2: float = Field(ge=0, le=1)
    s4: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _check_total_leaves_nonnegative_cash(self) -> QuotasConfig:
        total = self.s1 + self.s2 + self.s4
        if total > 1 + _QUOTA_SUM_TOLERANCE:
            raise ValueError(
                f"quotas 之和 {total:.6f} 超过 1（隐含现金底仓为负）："
                f"s1={self.s1}, s2={self.s2}, s4={self.s4}；"
                "见 spec §4.1 停用重归一规则，配额改动须保证三者之和 <= 1"
            )
        return self


class DriftBandConfig(StrictModel):
    s1: float = Field(ge=0, le=1)
    s2: float = Field(ge=0, le=1)
    s4: float = Field(ge=0, le=1)


class PerStockCapConfig(StrictModel):
    default: float = Field(gt=0, le=1)
    s4: float = Field(gt=0, le=1)


class RegimeConfig(StrictModel):
    """MarketRegimeGate 参数（IMPLEMENTATION_SPEC.md §4.3）。"""

    bull_exposure: float = Field(ge=0, le=1)
    range_exposure: float = Field(ge=0, le=1)
    bear_exposure: float = Field(ge=0, le=1)
    sma_n: int = Field(gt=0)
    mom_n: int = Field(gt=0)
    mom_bear: float
    mom_bull: float
    vol_n: int = Field(gt=0)
    vol_bull_max: float = Field(gt=0)
    dd_bear: float = Field(lt=0)
    confirm_days: int = Field(gt=0)


class DrawdownGateConfig(StrictModel):
    """PortfolioDrawdownGate 参数（IMPLEMENTATION_SPEC.md §4.3）。"""

    half: float = Field(lt=0)
    freeze: float = Field(lt=0)
    release: float = Field(lt=0)


class MarketPortfolioConfig(StrictModel):
    """单市场组合宪法配置（portfolio.yaml 的 CN / US 键）。"""

    quotas: QuotasConfig
    drift_band: DriftBandConfig
    per_stock_cap: PerStockCapConfig
    industry_cap_weight: float = Field(gt=0, le=1)
    regime: RegimeConfig
    dd_gate: DrawdownGateConfig


class LedgerGuardConfig(StrictModel):
    """账本失真防护阈值（IMPLEMENTATION_SPEC.md §4.3 末段）。"""

    unconfirmed_days: int = Field(gt=0)
    exec_rate_alert: float = Field(ge=0, le=1)
    exec_rate_window: int = Field(gt=0)


class PortfolioConfig(StrictModel):
    """portfolio.yaml 顶层模型。"""

    CN: MarketPortfolioConfig
    US: MarketPortfolioConfig
    ledger_guard: LedgerGuardConfig


def load_portfolio_config(path: Path | None = None) -> PortfolioConfig:
    """加载并校验 config/portfolio.yaml。"""
    yaml_path = path or (CONFIG_DIR / "portfolio.yaml")
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return PortfolioConfig.model_validate(raw)
