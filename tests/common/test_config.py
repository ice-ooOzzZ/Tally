"""T0.5.2 单测：config 加载、深合并/null 删除语义、非法配置报错定位到字段。"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from tally.common.config import (
    load_backtest_config,
    load_pool_config,
    load_portfolio_config,
    load_strategies_config,
    load_system_config,
)
from tally.common.config.merge import deep_merge
from tally.common.config.strategies import S1Params, S2Params, S4Params

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


@pytest.fixture(autouse=True)
def _dummy_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """system.yaml 的三个 `env:VAR` 密钥字段现在要求 `min_length=1`（M2 整改）。

    测试环境通常没有真实 `.env`；这里注入占位非空值，避免本模块里"测试系统配置
    其他字段"的用例因为密钥解析成空字符串而报出与被测字段无关的错误。真正校验
    "密钥缺失该怎么报错"的是下面 `test_system_config_empty_secret_rejected`。
    """
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy-tushare-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy-telegram-bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "dummy-telegram-chat-id")


# ---- 真实 config/*.yaml 能正确加载 --------------------------------------------


def test_load_system_config_real_file() -> None:
    cfg = load_system_config()
    assert cfg.markets["CN"].enabled is True
    assert cfg.markets["US"].enabled is False
    assert cfg.rate_limits.sec_edgar_rps == 8


def test_load_portfolio_config_real_file() -> None:
    cfg = load_portfolio_config()
    assert cfg.CN.quotas.s1 == pytest.approx(0.40)
    assert cfg.US.regime.vol_bull_max == pytest.approx(0.25)
    assert cfg.ledger_guard.exec_rate_window == 10


def test_load_pool_config_real_file() -> None:
    cfg = load_pool_config()
    assert cfg.CN.size == 50
    assert cfg.US.size == 30
    assert cfg.US.entry.roe_skip_if_missing is True


def test_load_backtest_config_real_file() -> None:
    cfg = load_backtest_config()
    assert cfg.oos.view_budget == 3
    assert cfg.costs.CN.stamp_duty[0].rate == pytest.approx(0.001)
    assert cfg.costs.US.capital_gains_tax.enabled is True


def test_load_strategies_config_real_file() -> None:
    resolved = load_strategies_config()
    s1_cn = resolved["s1_breakout"]["CN"]
    s1_us = resolved["s1_breakout"]["US"]
    assert isinstance(s1_cn, S1Params)
    assert isinstance(s1_us, S1Params)
    # CN 保留基准的涨停剔除条款
    assert s1_cn.entry.limit_up_exclude_ratio == pytest.approx(0.99)
    assert s1_cn.entry.daily_gain_exclude is None
    # US 深合并后：null 删除 limit_up_exclude_ratio，新增 daily_gain_exclude
    assert s1_us.entry.limit_up_exclude_ratio is None
    assert s1_us.entry.daily_gain_exclude == pytest.approx(0.10)
    # 未被 override 触碰的字段两个市场应一致
    assert s1_cn.entry.high_lookback == s1_us.entry.high_lookback == 250

    s2_us = resolved["s2_pead"]["US"]
    s2_cn = resolved["s2_pead"]["CN"]
    assert isinstance(s2_us, S2Params)
    assert isinstance(s2_cn, S2Params)
    assert s2_us.entry.r_mode == "price_inferred_2d"
    assert s2_cn.entry.r_mode is None

    s4_us = resolved["s4_panic_reversion"]["US"]
    s4_cn = resolved["s4_panic_reversion"]["CN"]
    assert isinstance(s4_us, S4Params)
    assert isinstance(s4_cn, S4Params)
    assert s4_us.entry.crash_day_drop == pytest.approx(-0.10)
    assert s4_cn.entry.crash_day_drop is None


def test_resolved_strategy_params_are_frozen() -> None:
    resolved = load_strategies_config()
    s1_cn = resolved["s1_breakout"]["CN"]
    assert isinstance(s1_cn, S1Params)
    with pytest.raises(ValidationError):
        s1_cn.entry.high_lookback = 999  # type: ignore[misc]


# ---- deep_merge 纯函数语义 ------------------------------------------------------


def test_deep_merge_overrides_scalar() -> None:
    base = {"a": 1, "b": 2}
    override = {"b": 20}
    assert deep_merge(base, override) == {"a": 1, "b": 20}


def test_deep_merge_null_deletes_key() -> None:
    base = {"a": 1, "b": 2}
    override = {"b": None}
    assert deep_merge(base, override) == {"a": 1}


def test_deep_merge_nested_dict_recurses() -> None:
    base = {"entry": {"x": 1, "y": 2}}
    override = {"entry": {"y": 20, "z": 3}}
    assert deep_merge(base, override) == {"entry": {"x": 1, "y": 20, "z": 3}}


def test_deep_merge_nested_null_deletes_nested_key() -> None:
    base = {"entry": {"x": 1, "y": 2}}
    override = {"entry": {"y": None}}
    assert deep_merge(base, override) == {"entry": {"x": 1}}


def test_deep_merge_list_replaced_wholesale_not_merged() -> None:
    base = {"items": [1, 2, 3]}
    override = {"items": [9]}
    assert deep_merge(base, override) == {"items": [9]}


def test_deep_merge_does_not_mutate_inputs() -> None:
    base = {"entry": {"x": 1}}
    override = {"entry": {"x": 2}}
    result = deep_merge(base, override)
    assert base == {"entry": {"x": 1}}
    assert override == {"entry": {"x": 2}}
    assert result == {"entry": {"x": 2}}


# ---- 非法配置报错定位到字段 -----------------------------------------------------


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _valid_system_dict() -> dict:
    raw = yaml.safe_load((CONFIG_DIR / "system.yaml").read_text(encoding="utf-8"))
    return raw


def test_system_config_missing_field_reports_field_path(tmp_path: Path) -> None:
    raw = _valid_system_dict()
    del raw["rate_limits"]["sec_edgar_rps"]
    path = _write_yaml(tmp_path, raw)
    with pytest.raises(ValidationError) as exc_info:
        load_system_config(path)
    locations = [".".join(str(p) for p in err["loc"]) for err in exc_info.value.errors()]
    assert "rate_limits.sec_edgar_rps" in locations


def test_system_config_wrong_type_reports_field_path(tmp_path: Path) -> None:
    raw = _valid_system_dict()
    raw["markets"]["CN"]["enabled"] = "not-a-bool-and-not-parseable"
    path = _write_yaml(tmp_path, raw)
    with pytest.raises(ValidationError) as exc_info:
        load_system_config(path)
    locations = [".".join(str(p) for p in err["loc"]) for err in exc_info.value.errors()]
    assert any("markets" in loc and "enabled" in loc for loc in locations)


def test_system_config_out_of_range_reports_field_path(tmp_path: Path) -> None:
    raw = _valid_system_dict()
    raw["markets"]["CN"]["cash_yield"] = 1.5  # 超出 [0,1]
    path = _write_yaml(tmp_path, raw)
    with pytest.raises(ValidationError) as exc_info:
        load_system_config(path)
    locations = [".".join(str(p) for p in err["loc"]) for err in exc_info.value.errors()]
    assert any("cash_yield" in loc for loc in locations)


def test_system_config_rejects_unknown_field_typo(tmp_path: Path) -> None:
    raw = _valid_system_dict()
    raw["rate_limits"]["tushare_per_minute_TYPO"] = 100  # 应被 extra=forbid 捕获
    path = _write_yaml(tmp_path, raw)
    with pytest.raises(ValidationError) as exc_info:
        load_system_config(path)
    locations = [".".join(str(p) for p in err["loc"]) for err in exc_info.value.errors()]
    assert any("tushare_per_minute_TYPO" in loc for loc in locations)


def test_system_config_missing_market_reports_error(tmp_path: Path) -> None:
    raw = _valid_system_dict()
    del raw["markets"]["US"]
    path = _write_yaml(tmp_path, raw)
    with pytest.raises(ValidationError):
        load_system_config(path)


def test_strategies_config_unknown_strategy_key_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load((CONFIG_DIR / "strategies.yaml").read_text(encoding="utf-8"))
    raw["s99_typo"] = raw["s1_breakout"]
    path = _write_yaml(tmp_path, raw)
    with pytest.raises(ValueError, match="s99_typo"):
        load_strategies_config(path)


def test_strategies_config_typo_in_market_override_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load((CONFIG_DIR / "strategies.yaml").read_text(encoding="utf-8"))
    raw["s1_breakout"]["market_overrides"]["US"]["entry"]["daily_gain_exclude_TYPO"] = 0.1
    path = _write_yaml(tmp_path, raw)
    with pytest.raises(ValidationError) as exc_info:
        load_strategies_config(path)
    locations = [".".join(str(p) for p in err["loc"]) for err in exc_info.value.errors()]
    assert any("daily_gain_exclude_TYPO" in loc for loc in locations)


# ---- M2: 密钥字段 min_length=1，空字符串必须被拒绝 --------------------------------


def test_system_config_empty_secret_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """`TUSHARE_TOKEN` 解析成空字符串（例如设置了变量但值为空）时应 fail-fast。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "")
    with pytest.raises(ValidationError) as exc_info:
        load_system_config()
    locations = [".".join(str(p) for p in err["loc"]) for err in exc_info.value.errors()]
    assert "tushare_token" in locations


def test_system_config_missing_env_var_resolves_to_empty_and_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完全不设置环境变量时，`resolve_env_ref` 落回空字符串，同样应被拒绝
    （而不是静默通过一份"看起来配置齐全但其实是空字符串"的密钥配置）。
    """
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        load_system_config()
    locations = [".".join(str(p) for p in err["loc"]) for err in exc_info.value.errors()]
    assert "tushare_token" in locations


# ---- M3: portfolio.yaml 配额之和不得超过 1（隐含现金底仓不得为负） ------------------


def test_portfolio_config_quotas_sum_over_one_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load((CONFIG_DIR / "portfolio.yaml").read_text(encoding="utf-8"))
    raw["CN"]["quotas"] = {"s1": 0.5, "s2": 0.4, "s4": 0.3}  # 合计 1.2 > 1
    path = _write_yaml(tmp_path, raw)
    with pytest.raises(ValidationError) as exc_info:
        load_portfolio_config(path)
    locations = [".".join(str(p) for p in err["loc"]) for err in exc_info.value.errors()]
    assert any("quotas" in loc for loc in locations)


def test_portfolio_config_quotas_sum_exactly_one_is_allowed(tmp_path: Path) -> None:
    """边界值：三者之和恰好为 1（现金底仓为 0）应当放行，不能因浮点误差误拒。"""
    raw = yaml.safe_load((CONFIG_DIR / "portfolio.yaml").read_text(encoding="utf-8"))
    raw["CN"]["quotas"] = {"s1": 0.5, "s2": 0.3, "s4": 0.2}  # 合计恰好 1.0
    path = _write_yaml(tmp_path, raw)
    cfg = load_portfolio_config(path)
    assert cfg.CN.quotas.s1 == pytest.approx(0.5)


# ---- 补测试：portfolio.yaml / pool.yaml / backtest.yaml 各补一条非法配置定位测试 ----


def test_portfolio_config_missing_field_reports_field_path(tmp_path: Path) -> None:
    raw = yaml.safe_load((CONFIG_DIR / "portfolio.yaml").read_text(encoding="utf-8"))
    del raw["ledger_guard"]["exec_rate_alert"]
    path = _write_yaml(tmp_path, raw)
    with pytest.raises(ValidationError) as exc_info:
        load_portfolio_config(path)
    locations = [".".join(str(p) for p in err["loc"]) for err in exc_info.value.errors()]
    assert "ledger_guard.exec_rate_alert" in locations


def test_pool_config_out_of_range_reports_field_path(tmp_path: Path) -> None:
    raw = yaml.safe_load((CONFIG_DIR / "pool.yaml").read_text(encoding="utf-8"))
    raw["US"]["entry"]["min_roe"] = "not-a-number"
    path = _write_yaml(tmp_path, raw)
    with pytest.raises(ValidationError) as exc_info:
        load_pool_config(path)
    locations = [".".join(str(p) for p in err["loc"]) for err in exc_info.value.errors()]
    assert any("min_roe" in loc for loc in locations)


def test_backtest_config_wrong_type_reports_field_path(tmp_path: Path) -> None:
    raw = yaml.safe_load((CONFIG_DIR / "backtest.yaml").read_text(encoding="utf-8"))
    raw["costs"]["CN"]["commission_min"] = "five-yuan"  # 应为数值
    path = _write_yaml(tmp_path, raw)
    with pytest.raises(ValidationError) as exc_info:
        load_backtest_config(path)
    locations = [".".join(str(p) for p in err["loc"]) for err in exc_info.value.errors()]
    assert any("commission_min" in loc for loc in locations)
