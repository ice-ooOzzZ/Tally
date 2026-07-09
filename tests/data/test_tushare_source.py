"""M1 T1.2 单测：`TushareSource` 回放测试 + 缺失 token loud failure + 重试。

硬性要求（CLAUDE.md「fixture 录制回放」+ IMPLEMENTATION_SPEC.md §11 T1.2 AC）：
本文件全部测试离线运行，不联网、不需要 `TUSHARE_TOKEN`、不 `import tushare`。
`autouse` fixture 显式 `delenv("TUSHARE_TOKEN")`，确保测试结果不依赖本机是否有
`.env`（本地开发机可能配了真 token，CI 一定没有——两种环境下结果必须一致）。
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable

import pandas as pd
import pytest

from tally.data.rate_limit import TokenBucket
from tally.data.sources import tushare_source as tushare_source_module
from tally.data.sources.tushare_source import (
    TransientTushareError,
    TushareAuthError,
    TushareSource,
    merge_daily_and_adj_factor,
)
from tests.data.tushare_fixtures import ReplayTushareTransport


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)


@pytest.fixture
def replay_transport() -> ReplayTushareTransport:
    return ReplayTushareTransport()


def _no_wait_bucket() -> TokenBucket:
    """限流不参与断言的测试用一个"永不需要等待"的桶：容量给够大，clock/sleep 用默认真实实现
    也无妨（因为永远不会触发 sleep），但为保险起见仍传入 no-op sleep。"""
    return TokenBucket(10_000.0, capacity=10_000.0, sleep=lambda _seconds: None)


def _source(transport: ReplayTushareTransport, **overrides: object) -> TushareSource:
    kwargs: dict[str, object] = {"rate_limiter": _no_wait_bucket(), "transport": transport}
    kwargs.update(overrides)
    return TushareSource(**kwargs)  # type: ignore[arg-type]


# ---- 回放：fetch_daily ---------------------------------------------------------


def test_fetch_daily_normalizes_columns_dtypes_and_units(
    replay_transport: ReplayTushareTransport,
) -> None:
    source = _source(replay_transport)

    df = source.fetch_daily(codes=["600000.SH"], start_date="20240101", end_date="20240104")

    assert list(df.columns) == [
        "code",
        "market",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "source",
    ]
    assert list(df["date"]) == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert (df["code"] == "600000").all()  # 交易所后缀已剥离
    assert (df["market"] == "CN").all()
    assert (df["source"] == "primary").all()
    row0 = df.iloc[0]
    assert row0["close"] == pytest.approx(9.90)
    # amount 单位换算：Tushare 千元 → 元
    assert row0["amount"] == pytest.approx(346500.12 * 1_000.0)
    assert df["open"].dtype == float
    assert df["close"].dtype == float


def test_fetch_daily_trade_date_batch_mode_returns_all_codes(
    replay_transport: ReplayTushareTransport,
) -> None:
    """§3.1 #1："全市场日频行情"按 trade_date 批量——不传 codes，只传 trade_date。"""
    source = _source(replay_transport)

    df = source.fetch_daily(trade_date="20240102")

    assert sorted(df["code"]) == ["000001", "600000"]


def test_fetch_daily_multiple_codes_concatenates_in_order(
    replay_transport: ReplayTushareTransport,
) -> None:
    source = _source(replay_transport)

    df = source.fetch_daily(codes=["600000.SH", "000001.SZ"], trade_date="20240102")

    assert list(df["code"]) == ["600000", "000001"]


def test_fetch_daily_no_match_returns_empty_dataframe_with_correct_columns(
    replay_transport: ReplayTushareTransport,
) -> None:
    source = _source(replay_transport)

    df = source.fetch_daily(codes=["999999.SH"], trade_date="20240102")

    assert df.empty
    assert list(df.columns) == [
        "code",
        "market",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "source",
    ]


# ---- 回放：fetch_adj_factor -----------------------------------------------------


def test_fetch_adj_factor_normalizes_columns(replay_transport: ReplayTushareTransport) -> None:
    source = _source(replay_transport)

    df = source.fetch_adj_factor(codes=["600000.SH"], start_date="20240101", end_date="20240104")

    assert list(df.columns) == ["code", "market", "date", "adj_factor"]
    assert list(df["adj_factor"]) == pytest.approx([1.2000, 1.2000, 1.2500])


# ---- 回放：fetch_daily_basic ----------------------------------------------------


def test_fetch_daily_basic_normalizes_valuation_columns(
    replay_transport: ReplayTushareTransport,
) -> None:
    source = _source(replay_transport)

    df = source.fetch_daily_basic(codes=["600000.SH"], trade_date="20240102")

    assert list(df.columns) == [
        "code",
        "market",
        "date",
        "pe_ttm",
        "pb",
        "market_cap",
        "turnover_amt",
    ]
    row = df.iloc[0]
    assert row["pe_ttm"] == pytest.approx(5.10)
    assert row["pb"] == pytest.approx(0.55)
    # market_cap 单位换算：Tushare total_mv 万元 → 元
    assert row["market_cap"] == pytest.approx(2_100_000.0 * 10_000.0)
    assert pd.isna(row["turnover_amt"])  # daily_basic 接口本身不提供成交额，见模块 docstring


# ---- 核心 AC：跨除权日/复权因子变化样本 -----------------------------------------


def test_fetch_kline_merges_daily_and_adj_factor_across_ex_dividend_event(
    replay_transport: ReplayTushareTransport,
) -> None:
    """600000.SH 在 01-03→01-04 间模拟除权：adj_factor 从 1.2000 跳到 1.2500；
    000001.SZ 全程 1.0500 不变（无事件对照组）。"""
    source = _source(replay_transport)

    df = source.fetch_kline(
        codes=["600000.SH", "000001.SZ"], start_date="20240101", end_date="20240104"
    )

    assert list(df.columns) == [
        "code",
        "market",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "adj_factor",
        "source",
    ]

    event_code = df[df["code"] == "600000"].sort_values("date")
    assert list(event_code["adj_factor"]) == pytest.approx([1.2000, 1.2000, 1.2500])
    # 除权当日行情列也要正确合并进同一行（不是只合并到某一列）
    ex_div_row = event_code[event_code["date"] == "2024-01-04"].iloc[0]
    assert ex_div_row["close"] == pytest.approx(10.20)
    assert ex_div_row["adj_factor"] == pytest.approx(1.2500)

    control_code = df[df["code"] == "000001"].sort_values("date")
    assert control_code["adj_factor"].nunique() == 1
    assert control_code["adj_factor"].iloc[0] == pytest.approx(1.0500)


def test_fetch_adj_factor_no_match_returns_empty_dataframe_with_correct_columns(
    replay_transport: ReplayTushareTransport,
) -> None:
    source = _source(replay_transport)

    df = source.fetch_adj_factor(codes=["999999.SH"], trade_date="20240102")

    assert df.empty
    assert list(df.columns) == ["code", "market", "date", "adj_factor"]


def test_fetch_daily_basic_no_match_returns_empty_dataframe_with_correct_columns(
    replay_transport: ReplayTushareTransport,
) -> None:
    source = _source(replay_transport)

    df = source.fetch_daily_basic(codes=["999999.SH"], trade_date="20240102")

    assert df.empty
    assert list(df.columns) == [
        "code",
        "market",
        "date",
        "pe_ttm",
        "pb",
        "market_cap",
        "turnover_amt",
    ]


def test_fetch_kline_no_match_returns_empty_dataframe_with_correct_columns(
    replay_transport: ReplayTushareTransport,
) -> None:
    source = _source(replay_transport)

    df = source.fetch_kline(codes=["999999.SH"], trade_date="20240102")

    assert df.empty
    assert list(df.columns) == [
        "code",
        "market",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "adj_factor",
        "source",
    ]


def test_trade_date_to_iso_rejects_malformed_input() -> None:
    with pytest.raises(ValueError, match="YYYYMMDD"):
        tushare_source_module._trade_date_to_iso("2024-01-02")
    with pytest.raises(ValueError, match="YYYYMMDD"):
        tushare_source_module._trade_date_to_iso("2024010")


def test_merge_daily_and_adj_factor_missing_adj_row_becomes_nan_not_dropped() -> None:
    """行情有该日但复权因子缺失（如新股上市首日）：该行保留，adj_factor 为 NaN。"""
    daily_df = pd.DataFrame(
        [
            {
                "code": "600000",
                "market": "CN",
                "date": "2024-01-05",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 100.0,
                "amount": 100.0,
                "source": "primary",
            }
        ]
    )
    adj_df = pd.DataFrame(columns=["code", "market", "date", "adj_factor"])

    merged = merge_daily_and_adj_factor(daily_df, adj_df)

    assert len(merged) == 1
    assert pd.isna(merged.iloc[0]["adj_factor"])


# ---- 缺失 token → loud failure；注入 transport 时不受影响 -----------------------


def test_missing_token_and_no_transport_raises_clear_error_only_when_calling() -> None:
    source = TushareSource(rate_limiter=_no_wait_bucket())  # 构造不报错

    with pytest.raises(TushareAuthError, match="TUSHARE_TOKEN"):
        source.fetch_daily(trade_date="20240102")


def test_missing_token_but_injected_transport_still_works(
    replay_transport: ReplayTushareTransport,
) -> None:
    source = TushareSource(rate_limiter=_no_wait_bucket(), transport=replay_transport)

    df = source.fetch_daily(codes=["600000.SH"], trade_date="20240102")

    assert len(df) == 1


def test_token_present_but_tushare_package_not_installed_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本仓库开发环境按任务要求只装 `--extra dev --extra dashboard`，不含 `tushare`
    包（在 `data` extra 里）；token 存在但包未安装时，也必须是清晰报错而非
    `ModuleNotFoundError` 裸抛到调用方。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token-for-test")
    source = TushareSource(rate_limiter=_no_wait_bucket())

    with pytest.raises(TushareAuthError, match="tushare"):
        source.fetch_daily(trade_date="20240102")


def test_explicit_token_param_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "env-token")
    source = TushareSource(rate_limiter=_no_wait_bucket(), token="")  # 显式传空覆盖 env

    with pytest.raises(TushareAuthError, match="TUSHARE_TOKEN"):
        source.fetch_daily(trade_date="20240102")


def test_real_transport_is_lazily_built_once_and_cached(
    monkeypatch: pytest.MonkeyPatch, replay_transport: ReplayTushareTransport
) -> None:
    """模拟"已安装 tushare 且 token 有效"：`ts.pro_api(token)` 只应在首次真正发起
    调用时惰性构建一次，后续调用复用同一个客户端（不重复鉴权/建连）。

    本仓库开发环境未装 `tushare` 包（见模块 docstring），因此这里用一个假的
    `sys.modules["tushare"]` 模块模拟"已安装"的情形，而不依赖真实 SDK/网络。
    """
    pro_api_calls: list[str] = []

    def _fake_pro_api(token: str) -> ReplayTushareTransport:
        pro_api_calls.append(token)
        return replay_transport

    fake_tushare_module = types.ModuleType("tushare")
    fake_tushare_module.pro_api = _fake_pro_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tushare", fake_tushare_module)
    monkeypatch.setenv("TUSHARE_TOKEN", "real-looking-token")

    source = TushareSource(rate_limiter=_no_wait_bucket())

    df1 = source.fetch_daily(codes=["600000.SH"], trade_date="20240102")
    df2 = source.fetch_adj_factor(codes=["600000.SH"], trade_date="20240102")

    assert len(df1) == 1
    assert len(df2) == 1
    assert pro_api_calls == ["real-looking-token"]  # 第二次调用复用缓存，未重新构建


# ---- 令牌桶集成：真的经过限流器 --------------------------------------------------


def test_fetch_daily_goes_through_shared_rate_limiter(
    replay_transport: ReplayTushareTransport,
) -> None:
    """容量=1、速率=60/分钟（=1/秒）的桶：连续两次 fetch（各消费 1 个令牌）必须
    触发一次等待，且等待时长符合速率模型——证明 `TushareSource` 真的把每次调用
    都路由过共享限流器，而不是绕开它。"""
    waits: list[float] = []
    now = [0.0]

    def fake_clock() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    bucket = TokenBucket(60.0, capacity=1.0, clock=fake_clock, sleep=fake_sleep)
    source = TushareSource(rate_limiter=bucket, transport=replay_transport)

    source.fetch_daily(codes=["600000.SH"], trade_date="20240102")
    assert waits == []  # 第一次调用：满桶，不等待

    source.fetch_daily(codes=["000001.SZ"], trade_date="20240102")
    assert waits == pytest.approx([1.0], abs=1e-6)  # 第二次调用：需补充 1 个令牌，等待约 1 秒


# ---- tenacity 重试：瞬时错误 ------------------------------------------------------


class _FlakyTransport:
    """前 N 次调用抛 `TransientTushareError`，之后成功返回 fixture 结果。"""

    def __init__(self, inner: ReplayTushareTransport, fail_times: int) -> None:
        self._inner = inner
        self._fail_times = fail_times
        self.call_count = 0

    def daily(self, **kwargs: str) -> pd.DataFrame:
        self.call_count += 1
        if self.call_count <= self._fail_times:
            raise TransientTushareError("模拟网络抖动")
        return self._inner.daily(**kwargs)

    def adj_factor(self, **kwargs: str) -> pd.DataFrame:
        return self._inner.adj_factor(**kwargs)

    def daily_basic(self, **kwargs: str) -> pd.DataFrame:
        return self._inner.daily_basic(**kwargs)


class _AlwaysFailingTransport:
    def __init__(self, exc_factory: Callable[[], Exception]) -> None:
        self._exc_factory = exc_factory
        self.call_count = 0

    def daily(self, **_kwargs: str) -> pd.DataFrame:
        self.call_count += 1
        raise self._exc_factory()

    def adj_factor(self, **_kwargs: str) -> pd.DataFrame:
        raise self._exc_factory()

    def daily_basic(self, **_kwargs: str) -> pd.DataFrame:
        raise self._exc_factory()


def test_retries_transient_error_then_succeeds(replay_transport: ReplayTushareTransport) -> None:
    flaky = _FlakyTransport(replay_transport, fail_times=2)
    source = TushareSource(
        rate_limiter=_no_wait_bucket(),
        transport=flaky,  # type: ignore[arg-type]
        retry_attempts=3,
        retry_sleep=lambda _seconds: None,  # 不真 sleep
    )

    df = source.fetch_daily(codes=["600000.SH"], trade_date="20240102")

    assert flaky.call_count == 3  # 前 2 次失败 + 第 3 次成功
    assert len(df) == 1


def test_retries_exhausted_raises_transient_error(replay_transport: ReplayTushareTransport) -> None:
    flaky = _FlakyTransport(replay_transport, fail_times=99)  # 永远失败
    source = TushareSource(
        rate_limiter=_no_wait_bucket(),
        transport=flaky,  # type: ignore[arg-type]
        retry_attempts=3,
        retry_sleep=lambda _seconds: None,
    )

    with pytest.raises(TransientTushareError):
        source.fetch_daily(codes=["600000.SH"], trade_date="20240102")

    assert flaky.call_count == 3  # 恰好重试到上限次数，不多不少


def test_non_transient_looking_exception_is_wrapped_and_retried_then_raised() -> None:
    """转输层抛出的任何异常（哪怕不是 `TransientTushareError`）都会被 `_call_once`
    统一包装为 `TransientTushareError` 纳入重试；到达上限后仍能通过异常链看到
    原始异常（`__cause__`）。"""
    always_failing = _AlwaysFailingTransport(lambda: ValueError("某个意料之外的 bug"))
    source = TushareSource(
        rate_limiter=_no_wait_bucket(),
        transport=always_failing,  # type: ignore[arg-type]
        retry_attempts=2,
        retry_sleep=lambda _seconds: None,
    )

    with pytest.raises(TransientTushareError) as exc_info:
        source.fetch_daily(codes=["600000.SH"], trade_date="20240102")

    assert always_failing.call_count == 2
    assert isinstance(exc_info.value.__cause__, ValueError)
