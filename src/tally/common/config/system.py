"""system.yaml 的 pydantic 模型与加载函数（IMPLEMENTATION_SPEC.md §10）。"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field, field_validator

from tally.common.config.base import CONFIG_DIR, Market, StrictModel, resolve_env_ref


class MarketFinanceConfig(StrictModel):
    """单个市场的资金与基准配置（system.yaml 的 markets.<MARKET>）。"""

    enabled: bool
    initial_capital: float = Field(gt=0)
    benchmark: str = Field(min_length=1)
    cash_yield: float = Field(ge=0, le=1)


class RateLimitsConfig(StrictModel):
    """全局令牌桶参数（system.yaml 的 rate_limits）。"""

    tushare_per_min: int = Field(gt=0)
    akshare_concurrency: int = Field(gt=0)
    akshare_interval_s: float = Field(ge=0)
    yfinance_concurrency: int = Field(gt=0)
    sec_edgar_rps: int = Field(gt=0)


class SystemConfig(StrictModel):
    """system.yaml 顶层模型。

    三个密钥字段要求 `min_length=1`：只保证"解析出的字符串非空"这一层校验
    （对齐 `sec_user_agent` 的既有风格），不代表已校验密钥本身有效/可用。
    "密钥完全缺失时给出更友好的报错"属于更丰富的启动期检查，留给 M1 实际
    使用这些密钥的 data/sources 适配器落地（例如连接测试失败时的错误信息），
    本层只负责 fail-fast：环境变量缺失 → `resolve_env_ref` 解析为空字符串 →
    此处 `min_length=1` 立即拒绝，不允许"看起来配置齐全但其实是空字符串"
    的配置静默通过。
    """

    markets: dict[Market, MarketFinanceConfig]
    tushare_token: str = Field(min_length=1)
    telegram_bot_token: str = Field(min_length=1)
    telegram_chat_id: str = Field(min_length=1)
    sec_user_agent: str = Field(min_length=1)
    rate_limits: RateLimitsConfig

    @field_validator("tushare_token", "telegram_bot_token", "telegram_chat_id", mode="before")
    @classmethod
    def _resolve_env(cls, value: object) -> object:
        return resolve_env_ref(value)

    @field_validator("markets")
    @classmethod
    def _require_cn_and_us(
        cls, value: dict[Market, MarketFinanceConfig]
    ) -> dict[Market, MarketFinanceConfig]:
        missing = {"CN", "US"} - value.keys()
        if missing:
            raise ValueError(f"markets 缺少市场配置：{sorted(missing)}")
        return value


def load_system_config(path: Path | None = None) -> SystemConfig:
    """加载并校验 config/system.yaml。"""
    yaml_path = path or (CONFIG_DIR / "system.yaml")
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return SystemConfig.model_validate(raw)
