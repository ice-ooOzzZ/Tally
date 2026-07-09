"""M1 T1.3 单测：`WatchlistConfig`/`load_watchlist_config`——真实文件加载 + 校验规则。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from tally.common.config import WatchlistConfig, load_watchlist_config

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


def test_load_watchlist_config_real_file() -> None:
    cfg = load_watchlist_config()

    assert cfg.market == "CN"
    assert cfg.start_date == date(2024, 1, 1)
    assert len(cfg.codes) >= 20
    assert all(code.endswith((".SH", ".SZ")) for code in cfg.codes)
    assert "600000.SH" in cfg.codes


def test_watchlist_config_rejects_duplicate_codes() -> None:
    raw = {"market": "CN", "start_date": "2024-01-01", "codes": ["600000.SH", "600000.SH"]}

    with pytest.raises(ValidationError, match="重复"):
        WatchlistConfig.model_validate(raw)


def test_watchlist_config_rejects_empty_codes() -> None:
    raw = {"market": "CN", "start_date": "2024-01-01", "codes": []}

    with pytest.raises(ValidationError):
        WatchlistConfig.model_validate(raw)


def test_watchlist_config_rejects_unknown_field() -> None:
    raw = {
        "market": "CN",
        "start_date": "2024-01-01",
        "codes": ["600000.SH"],
        "unexpected_field": True,
    }

    with pytest.raises(ValidationError):
        WatchlistConfig.model_validate(raw)


def test_watchlist_config_is_frozen() -> None:
    cfg = WatchlistConfig(market="CN", start_date=date(2024, 1, 1), codes=("600000.SH",))

    with pytest.raises(ValidationError):
        cfg.market = "US"  # type: ignore[misc]


def test_load_watchlist_config_from_custom_path(tmp_path: Path) -> None:
    custom_path = tmp_path / "watchlist.yaml"
    custom_path.write_text(
        yaml.safe_dump({"market": "CN", "start_date": "2023-01-01", "codes": ["600000.SH"]}),
        encoding="utf-8",
    )

    cfg = load_watchlist_config(custom_path)

    assert cfg.start_date == date(2023, 1, 1)
    assert cfg.codes == ("600000.SH",)
