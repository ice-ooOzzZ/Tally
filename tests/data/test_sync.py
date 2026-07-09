"""M1 T1.3 单测：`SyncEngine`——名单增量同步、防未来函数、跨除权日复权连续、单票容错。

硬性要求（CLAUDE.md「fixture 录制回放」+ IMPLEMENTATION_SPEC.md §11 T1.3 AC）：
本文件全部测试离线运行，不联网、不需要 `TUSHARE_TOKEN`、不 `import tushare`。
沿用 T1.2 的 `ReplayTushareTransport` + `tests/fixtures/tushare/*.json`（600000.SH
在 01-03→01-04 间的 adj_factor 1.2000→1.2500 即为本任务复用的除权事件样本）。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from tally.common.config.base import Market
from tally.data.rate_limit import RateLimiterRegistry, TokenBucket
from tally.data.repository import Repository
from tally.data.sync import CodeSyncResult, SyncEngine
from tests.data.tushare_fixtures import ReplayTushareTransport

_MARKET: Market = "CN"
_NO_WAIT_RATE_PER_MIN = 1_000_000.0


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repository]:
    repository = Repository(tmp_path / "tally.db")
    try:
        yield repository
    finally:
        repository.close()


def _registry() -> RateLimiterRegistry:
    """给测试用的"永不需要等待"限流器注册表（容量给够大，与 T1.2 单测同一惯例）。"""
    registry = RateLimiterRegistry()
    registry.register(
        "tushare",
        TokenBucket(_NO_WAIT_RATE_PER_MIN, capacity=_NO_WAIT_RATE_PER_MIN, sleep=lambda _s: None),
    )
    return registry


def _engine(codes: list[str], start: date) -> SyncEngine:
    return SyncEngine(
        codes=codes,
        initial_start_date=start,
        rate_limiter_registry=_registry(),
        tushare_rate_per_min=_NO_WAIT_RATE_PER_MIN,
        tushare_retry_sleep=lambda _seconds: None,  # 不真 sleep（同 T1.2 单测惯例）
    )


# ---- 测试用 transport 包装：记录调用参数 / 模拟"忽略 end_date"缺陷 / 模拟单票失败 --------


class _RecordingTransport:
    """透传给内层 transport，同时记录每次调用的端点与参数，供断言"只请求了缺失区间"。"""

    def __init__(self, inner: ReplayTushareTransport) -> None:
        self._inner = inner
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def daily(self, **kwargs: str) -> pd.DataFrame:
        self.calls.append(("daily", dict(kwargs)))
        return self._inner.daily(**kwargs)

    def adj_factor(self, **kwargs: str) -> pd.DataFrame:
        self.calls.append(("adj_factor", dict(kwargs)))
        return self._inner.adj_factor(**kwargs)

    def daily_basic(self, **kwargs: str) -> pd.DataFrame:
        self.calls.append(("daily_basic", dict(kwargs)))
        return self._inner.daily_basic(**kwargs)


class _LeakyEndDateTransport:
    """模拟"transport 未正确遵守 end_date、多返回了 as_of_date 之后的数据"这一缺陷场景，
    用来证明 sync 层自己的截断（`_clip_to_as_of`）才是真正生效的防线。"""

    def __init__(self, inner: ReplayTushareTransport) -> None:
        self._inner = inner

    def daily(self, **kwargs: str) -> pd.DataFrame:
        return self._inner.daily(**{**kwargs, "end_date": ""})

    def adj_factor(self, **kwargs: str) -> pd.DataFrame:
        return self._inner.adj_factor(**{**kwargs, "end_date": ""})

    def daily_basic(self, **kwargs: str) -> pd.DataFrame:
        return self._inner.daily_basic(**{**kwargs, "end_date": ""})


class _BoomTransport:
    """任何端点被调用就失败——用来证明空名单场景确实不发起任何调用。"""

    def daily(self, **_kwargs: str) -> pd.DataFrame:
        raise AssertionError("空名单不应调用 transport.daily()")

    def adj_factor(self, **_kwargs: str) -> pd.DataFrame:
        raise AssertionError("空名单不应调用 transport.adj_factor()")

    def daily_basic(self, **_kwargs: str) -> pd.DataFrame:
        raise AssertionError("空名单不应调用 transport.daily_basic()")


class _FailingForCodeTransport:
    """指定 ts_code 的 `daily()` 调用恒失败，模拟"某票拉取失败"；其余票正常透传。"""

    def __init__(self, inner: ReplayTushareTransport, fail_ts_code: str) -> None:
        self._inner = inner
        self._fail_ts_code = fail_ts_code

    def daily(self, **kwargs: str) -> pd.DataFrame:
        if kwargs.get("ts_code") == self._fail_ts_code:
            raise RuntimeError(f"模拟 {self._fail_ts_code} 拉取失败")
        return self._inner.daily(**kwargs)

    def adj_factor(self, **kwargs: str) -> pd.DataFrame:
        return self._inner.adj_factor(**kwargs)

    def daily_basic(self, **kwargs: str) -> pd.DataFrame:
        return self._inner.daily_basic(**kwargs)


# ---- 核心 AC：跨除权日增量更新后复权收益序列连续 ---------------------------------------


def test_incremental_sync_across_ex_dividend_event_keeps_adjusted_return_continuous(
    repo: Repository,
) -> None:
    """600000.SH：先同步到 01-03（adj_factor 恒 1.2000），再增量同步拉入 01-04
    （除权发生，adj_factor 跳到 1.2500）。断言：两次落库拼接后的复权收益序列
    （close × adj_factor 归一后的逐日收益率）在 01-03→01-04 边界连续——没有
    NaN/断裂，且数值等于用原始 fixture 手工重算的预期值。"""
    engine = _engine(["600000.SH"], start=date(2024, 1, 2))
    transport = ReplayTushareTransport()

    engine.sync(market=_MARKET, as_of_date=date(2024, 1, 3), transport=transport, repo=repo)
    engine.sync(market=_MARKET, as_of_date=date(2024, 1, 4), transport=transport, repo=repo)

    kline = repo.get_kline("600000", _MARKET)
    assert list(kline["date"]) == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert list(kline["adj_factor"]) == pytest.approx([1.2000, 1.2000, 1.2500])

    adjusted_close = (kline["close"] * kline["adj_factor"]).tolist()
    returns = [adjusted_close[i] / adjusted_close[i - 1] - 1 for i in range(1, len(adjusted_close))]

    expected_boundary_return = (10.20 * 1.2500) / (10.50 * 1.2000) - 1
    assert returns[-1] == pytest.approx(expected_boundary_return)
    assert all(r == r for r in returns)  # 无 NaN（NaN != NaN）：序列连续，未被增量落库割裂


def test_incremental_sync_only_requests_missing_trading_days(repo: Repository) -> None:
    """第二次 sync 必须只请求缺失区间（01-04），不得重新请求已有历史（01-02/01-03）。"""
    inner = ReplayTushareTransport()
    transport = _RecordingTransport(inner)
    engine = _engine(["600000.SH"], start=date(2024, 1, 2))

    engine.sync(market=_MARKET, as_of_date=date(2024, 1, 3), transport=transport, repo=repo)
    first_call_start_dates = {kwargs["start_date"] for _name, kwargs in transport.calls}
    assert first_call_start_dates == {"20240102"}  # 首次同步：从 initial_start_date 起

    transport.calls.clear()
    engine.sync(market=_MARKET, as_of_date=date(2024, 1, 4), transport=transport, repo=repo)

    assert transport.calls  # 确有发起调用（不是"因为已同步完成而短路跳过"）
    for _name, kwargs in transport.calls:
        assert kwargs["start_date"] == "20240104"
        assert kwargs["end_date"] == "20240104"


def test_second_sync_at_same_as_of_date_is_noop(repo: Repository) -> None:
    """已同步到 as_of_date 后再次以同一 as_of_date 调用 sync：缺失区间为空，
    不应发起任何 transport 调用。"""
    engine = _engine(["600000.SH"], start=date(2024, 1, 2))
    engine.sync(
        market=_MARKET, as_of_date=date(2024, 1, 3), transport=ReplayTushareTransport(), repo=repo
    )

    summary = engine.sync(
        market=_MARKET, as_of_date=date(2024, 1, 3), transport=_BoomTransport(), repo=repo
    )

    assert summary.results == (CodeSyncResult(code="600000.SH"),)


# ---- 增量：首次同步（无历史）也要正确 ---------------------------------------------------


def test_first_sync_with_no_history_pulls_full_initial_range(repo: Repository) -> None:
    engine = _engine(["600000.SH", "000001.SZ"], start=date(2024, 1, 2))

    summary = engine.sync(
        market=_MARKET, as_of_date=date(2024, 1, 4), transport=ReplayTushareTransport(), repo=repo
    )

    assert summary.failed == ()
    assert summary.total_kline_rows == 6  # 2 只票 × 3 个交易日
    kline_600000 = repo.get_kline("600000", _MARKET)
    assert list(kline_600000["date"]) == ["2024-01-02", "2024-01-03", "2024-01-04"]
    valuation_600000 = repo.get_valuation("600000", _MARKET)
    assert list(valuation_600000["date"]) == ["2024-01-02", "2024-01-03", "2024-01-04"]


def test_turnover_amt_filled_from_daily_amount(repo: Repository) -> None:
    """T1.2 遗留约定：daily_basic 本身不提供成交额，turnover_amt 从 daily.amount 关联填充。"""
    engine = _engine(["600000.SH"], start=date(2024, 1, 2))

    engine.sync(
        market=_MARKET, as_of_date=date(2024, 1, 2), transport=ReplayTushareTransport(), repo=repo
    )

    valuation = repo.get_valuation("600000", _MARKET)
    assert len(valuation) == 1
    # daily.json 600000.SH 01-02 amount=346500.12（千元）→ 元
    assert valuation.iloc[0]["turnover_amt"] == pytest.approx(346500.12 * 1_000.0)


# ---- 防未来函数（铁律1） ---------------------------------------------------------------


def test_sync_never_persists_data_beyond_as_of_date_even_if_transport_returns_it(
    repo: Repository,
) -> None:
    """`_LeakyEndDateTransport` 完全不理会 `end_date`，对 600000.SH 的任何调用都会把
    01-02/01-03/01-04 三天全部返回；但 `as_of_date=01-03` 时，01-04 绝不能被落库——
    证明防未来函数的截断不依赖 transport 是否正确遵守 end_date。"""
    engine = _engine(["600000.SH"], start=date(2024, 1, 2))
    transport = _LeakyEndDateTransport(ReplayTushareTransport())

    summary = engine.sync(
        market=_MARKET, as_of_date=date(2024, 1, 3), transport=transport, repo=repo
    )

    assert summary.failed == ()
    kline = repo.get_kline("600000", _MARKET)
    assert list(kline["date"]) == ["2024-01-02", "2024-01-03"]
    assert "2024-01-04" not in set(kline["date"])

    valuation = repo.get_valuation("600000", _MARKET)
    assert list(valuation["date"]) == ["2024-01-02", "2024-01-03"]


# ---- 健壮性：名单为空 / 单票失败不阻塞其他票 --------------------------------------------


def test_empty_watchlist_returns_empty_summary_without_any_transport_call(repo: Repository) -> None:
    engine = _engine([], start=date(2024, 1, 2))

    summary = engine.sync(
        market=_MARKET, as_of_date=date(2024, 1, 3), transport=_BoomTransport(), repo=repo
    )

    assert summary.results == ()
    assert summary.total_kline_rows == 0
    assert summary.total_valuation_rows == 0


def test_code_with_no_fixture_data_yields_zero_rows_without_error(repo: Repository) -> None:
    """名单里的票在 fixture 里完全没有数据（如新股/代码打错）：`fetch_kline`/
    `fetch_daily_basic` 均返回空表，`_clip_to_as_of`/`_fill_turnover_amt` 的
    "空表直接返回"分支被触发，整体应正常完成（0 行，非失败）而不是抛异常。"""
    engine = _engine(["999999.SH"], start=date(2024, 1, 2))

    summary = engine.sync(
        market=_MARKET, as_of_date=date(2024, 1, 4), transport=ReplayTushareTransport(), repo=repo
    )

    assert summary.failed == ()
    result = summary.results[0]
    assert result.ok
    assert result.kline_rows == 0
    assert result.valuation_rows == 0


def test_one_code_failure_does_not_block_other_codes(repo: Repository) -> None:
    transport = _FailingForCodeTransport(ReplayTushareTransport(), fail_ts_code="600000.SH")
    engine = _engine(["600000.SH", "000001.SZ"], start=date(2024, 1, 2))

    summary = engine.sync(
        market=_MARKET, as_of_date=date(2024, 1, 4), transport=transport, repo=repo
    )

    failed_codes = {r.code for r in summary.failed}
    assert failed_codes == {"600000.SH"}
    assert summary.failed[0].error is not None

    ok_result = next(r for r in summary.results if r.code == "000001.SZ")
    assert ok_result.ok
    assert ok_result.kline_rows == 3

    assert repo.get_kline("600000", _MARKET).empty  # 失败票未写入任何脏数据
    assert len(repo.get_kline("000001", _MARKET)) == 3  # 其他票正常完成同步
