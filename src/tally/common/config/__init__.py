"""配置子系统：pydantic v2 加载 IMPLEMENTATION_SPEC.md §10 的全部 yaml。

对外只暴露 load_* 函数与模型类型；深合并细节见 merge.py，`env:VAR` 解析见 base.py。
"""

from tally.common.config.backtest import BacktestConfig, load_backtest_config
from tally.common.config.base import CONFIG_DIR, Market, StrictModel, resolve_env_ref
from tally.common.config.merge import deep_merge
from tally.common.config.pool import PoolConfig, load_pool_config
from tally.common.config.portfolio import PortfolioConfig, load_portfolio_config
from tally.common.config.strategies import ResolvedStrategies, load_strategies_config
from tally.common.config.system import SystemConfig, load_system_config

__all__ = [
    "CONFIG_DIR",
    "Market",
    "StrictModel",
    "resolve_env_ref",
    "deep_merge",
    "SystemConfig",
    "load_system_config",
    "PortfolioConfig",
    "load_portfolio_config",
    "PoolConfig",
    "load_pool_config",
    "BacktestConfig",
    "load_backtest_config",
    "ResolvedStrategies",
    "load_strategies_config",
]
