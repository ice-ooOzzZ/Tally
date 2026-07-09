"""MarketProfile 单测：CN/US 逐字段断言（IMPLEMENTATION_SPEC.md §5.1.1）。

补测试缺口：M0.5 之前 market_profile.py 只有间接覆盖（calendar.py 单测顺带用到
calendar_name），CN/US 的能力开关字段本身零断言。
"""

import pytest

from tally.common.market_profile import MarketProfile, get_market_profile


def test_cn_profile_fields() -> None:
    profile = get_market_profile("CN")
    assert profile.market == "CN"
    assert profile.currency == "CNY"
    assert profile.benchmark == "H00300"
    assert profile.calendar_name == "SSE"
    assert profile.industry_taxonomy == "SW_L1"
    assert profile.has_price_limit is True
    assert profile.t_plus_one is True
    assert profile.fin_effective_rule == "announce_plus_1td"
    assert profile.has_scheduled_disclosure is True
    assert profile.event_date_mode == "announced"


def test_us_profile_fields() -> None:
    profile = get_market_profile("US")
    assert profile.market == "US"
    assert profile.currency == "USD"
    assert profile.benchmark == "^SP500TR"
    assert profile.calendar_name == "NYSE"
    assert profile.industry_taxonomy == "GICS_SECTOR"
    assert profile.has_price_limit is False
    assert profile.t_plus_one is False
    assert profile.fin_effective_rule == "filed_plus_1td"
    assert profile.has_scheduled_disclosure is False
    assert profile.event_date_mode == "price_inferred"


def test_profile_is_frozen() -> None:
    profile = get_market_profile("CN")
    with pytest.raises(AttributeError):
        profile.market = "US"  # type: ignore[misc]


def test_unknown_market_raises_value_error() -> None:
    with pytest.raises(ValueError, match="未知市场"):
        get_market_profile("JP")  # type: ignore[arg-type]


def test_cn_and_us_differ_on_every_capability_flag() -> None:
    """CN/US 在每一个能力开关字段上都应不同——如果某个字段两市场取值相同，
    说明该字段大概率没有真正表达市场差异（写死了同一个值），值得再核查一次。
    """
    cn = get_market_profile("CN")
    us = get_market_profile("US")
    capability_fields = (
        "currency",
        "benchmark",
        "calendar_name",
        "industry_taxonomy",
        "has_price_limit",
        "t_plus_one",
        "fin_effective_rule",
        "has_scheduled_disclosure",
        "event_date_mode",
    )
    for field_name in capability_fields:
        assert getattr(cn, field_name) != getattr(us, field_name), field_name


def test_market_profile_is_dataclass_with_expected_field_names() -> None:
    expected_fields = {
        "market",
        "currency",
        "benchmark",
        "calendar_name",
        "industry_taxonomy",
        "has_price_limit",
        "t_plus_one",
        "fin_effective_rule",
        "has_scheduled_disclosure",
        "event_date_mode",
    }
    actual_fields = {f.name for f in MarketProfile.__dataclass_fields__.values()}
    assert actual_fields == expected_fields
