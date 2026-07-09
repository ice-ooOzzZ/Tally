"""最小同步管道（IMPLEMENTATION_SPEC.md §11 T1.3）：手工名单 → 日K+估值最小同步。

范围与边界：
- **只做"名单 × [缺失区间] → Tushare 拉取 → Repository 落库"这一条最小链路**，
  不涉及 §6 的自动筛池（M2 T2.x）、§3.3 的 `sync_failures` 补拉持久化与
  连续失败告警（留给后续任务；本任务的"某票失败不阻塞其他票"只做到"单次
  运行内跳过并记录原因"这一层，见 `CodeSyncResult.error`）。
- **增量按缺失区间**（§3.3）：每只票独立判定——分别查 Repository 该票已有
  kline / valuation 各自的最新日期，取两者**较小值**的下一交易日作为续传
  起点，只拉 `[起点, as_of_date]` 这段区间；两表中任一张对该票完全没有数据时
  （包括首次同步，以及"kline 已落库但 valuation 从未成功写入过"这类历史遗留
  的部分失败）用 `WatchlistConfig.start_date`（或调用方显式传入的
  `initial_start_date`）整段重新拉取——`upsert_kline` 是幂等的，重复写入已有
  的 kline 行不会有副作用。这是"kline/valuation 非原子写入"场景下的**自愈式
  续传**：任一表的缺口都会在下一次 sync 被自动重新拉取补齐，见
  `_resolve_missing_start`。区间为空（两表都已同步到 `as_of_date`）时该票
  直接跳过，不发起任何调用——但这也是"`as_of_date` 早于 Repository 已有数据
  最新日期"这一异常调用形态的同一个 no-op 分支，见 `_sync_one_code_unsafe`
  内的说明。
- **防未来函数（铁律1）**：区间上界已用 `as_of_date` 截断；额外在拉取结果落库
  前再做一次 `date <= as_of_date` 的显式过滤（`_clip_to_as_of`）——双重防线，
  不依赖 transport 是否正确遵守 `end_date` 参数（回放/真实 transport 出现
  "多返回了 as_of_date 之后的行"这类缺陷时，仍不会被写入 Repository）。
- **turnover_amt 从 daily.amount 关联填充**（T1.2 模块 docstring 遗留约定：
  `daily_basic` 接口本身不提供成交额）：按 `(code, market, date)` 左连接
  `fetch_kline` 结果的 `amount` 列到 `fetch_daily_basic` 结果的 `turnover_amt`。
- **所有持久化经 Repository**（铁律3）：本模块不出现裸 SQL，只调用
  `Repository.upsert_kline`/`upsert_valuation`。
- **令牌桶复用**：`TushareSource` 的限流器经
  `RateLimiterRegistry.get_or_create("tushare", ...)` 取得/复用，同进程内
  与其他调用方共享同一份限流状态（§3.3"全局令牌桶"）。
- **transport 依赖注入**：`SyncEngine.sync()` 的 `transport` 参数直接注入
  `TushareTransport`（而非已装配好的 `TushareSource`）——单测注入
  `tests.data.tushare_fixtures.ReplayTushareTransport`（或其包装），
  全程离线、不需要 `TUSHARE_TOKEN`、不 import 真实 `tushare` 包。
- 串行实现（M1 最小实现；名单仅 20 只票，串行足够）：每只票的拉取相互独立，
  为后续可能的并发化（`ThreadPoolExecutor`）预留结构——`_sync_one_code` 不
  依赖调用顺序、不共享可写状态（`Repository`/`TokenBucket` 均自带线程安全）。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

from tally.common.calendar import next_trading_day, trading_days_in_range
from tally.common.config.base import Market
from tally.data.rate_limit import RateLimiterRegistry, Sleep
from tally.data.repository import Repository
from tally.data.sources.tushare_source import TushareSource, TushareTransport

_TUSHARE_SOURCE_NAME = "tushare"

# valuation 表列顺序（对齐 schema.sql / tushare_source._VALUATION_COLUMNS）；
# 本模块需要在填充 turnover_amt 后重新对齐这份列序，故显式声明一份本地常量，
# 不反向 import tushare_source 的私有列常量（避免跨模块耦合到对方的实现细节）。
_VALUATION_COLUMN_ORDER = ("code", "market", "date", "pe_ttm", "pb", "market_cap", "turnover_amt")


@dataclass(frozen=True)
class CodeSyncResult:
    """单只票一次 `sync()` 调用的结果。

    契约（合并审查 HIGH 修复）：`kline_rows`/`valuation_rows` 必须如实反映本次
    调用**实际写入 Repository 的行数**，即使 `error is not None`——例如
    kline 已成功提交、valuation 提交失败的部分失败场景，`kline_rows` 仍报告
    kline 那次提交的真实行数，不得因为整体判失败就归零。调用方据此即可知道
    "哪张表已经落库、哪张表还缺"，而不必再反查 Repository。
    """

    code: str
    kline_rows: int = 0
    valuation_rows: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class SyncSummary:
    """一次 `sync()` 调用（覆盖名单内所有票）的汇总结果。"""

    market: Market
    as_of_date: date
    results: tuple[CodeSyncResult, ...]

    @property
    def failed(self) -> tuple[CodeSyncResult, ...]:
        return tuple(r for r in self.results if not r.ok)

    @property
    def total_kline_rows(self) -> int:
        return sum(r.kline_rows for r in self.results)

    @property
    def total_valuation_rows(self) -> int:
        return sum(r.valuation_rows for r in self.results)


def _strip_exchange_suffix(code: str) -> str:
    """`"600000.SH"` → `"600000"`：对齐 Repository 的 `code` 列（不带交易所后缀）。"""
    return code.split(".", 1)[0]


def _clip_to_as_of(df: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    """防未来函数第二道防线：无论 transport 是否遵守 `end_date`，落库前再截断一次。"""
    if df.empty:
        return df
    as_of_iso = as_of_date.isoformat()
    return df[df["date"] <= as_of_iso].reset_index(drop=True)


_MERGE_KEY_COLUMNS = ("code", "market", "date")


def _assert_no_duplicate_keys(df: pd.DataFrame, *, label: str) -> None:
    """M3（合并审查 MEDIUM）：`_fill_turnover_amt` 的关联以 `(code, market, date)`
    为键，一旦上游（transport/回放 fixture）异常返回了重复行，`merge` 会把
    valuation 一行静默展开成多行、turnover_amt 被错配到不属于它的行——这类
    静默错配不会报错，只会在落库后产生看似正常但实际串号的数据。落库前显式
    断言键唯一，出现重复直接失败而不是带着错配数据继续跑。"""
    if df.empty:
        return
    duplicated_mask = df.duplicated(subset=list(_MERGE_KEY_COLUMNS), keep=False)
    if duplicated_mask.any():
        dup_keys = (
            df.loc[duplicated_mask, list(_MERGE_KEY_COLUMNS)].drop_duplicates().to_dict("records")
        )
        raise ValueError(
            f"{label} 存在重复的 (code, market, date) 行，拒绝落库以防止 turnover_amt "
            f"静默错配：{dup_keys}"
        )


def _fill_turnover_amt(valuation_df: pd.DataFrame, kline_df: pd.DataFrame) -> pd.DataFrame:
    """把 `kline_df.amount` 按 `(code, market, date)` 关联填充进
    `valuation_df.turnover_amt`（T1.2 模块 docstring 遗留约定，见本文件顶部说明）。
    """
    if valuation_df.empty:
        return valuation_df
    _assert_no_duplicate_keys(kline_df, label="kline_df")
    _assert_no_duplicate_keys(valuation_df, label="valuation_df")
    amount_lookup = kline_df[["code", "market", "date", "amount"]].rename(
        columns={"amount": "turnover_amt"}
    )
    merged = valuation_df.drop(columns=["turnover_amt"]).merge(
        amount_lookup, on=["code", "market", "date"], how="left"
    )
    return merged[list(_VALUATION_COLUMN_ORDER)]


class SyncEngine:
    """手工名单 → 日K+估值最小同步引擎（增量、`as_of_date` 贯穿）。"""

    def __init__(
        self,
        *,
        codes: Sequence[str],
        initial_start_date: date,
        rate_limiter_registry: RateLimiterRegistry,
        tushare_rate_per_min: float,
        tushare_retry_sleep: Sleep = time.sleep,
    ) -> None:
        """
        Args:
            codes: 观察名单（Tushare `ts_code` 格式，带交易所后缀），通常来自
                `WatchlistConfig.codes`；可为空（`sync()` 此时直接返回空汇总，
                不发起任何调用）。
            initial_start_date: 某票在 Repository 内尚无历史数据时的首次同步
                起点，通常来自 `WatchlistConfig.start_date`。
            rate_limiter_registry: 与其他调用方共享的限流器注册表。
            tushare_rate_per_min: 传给 `RateLimiterRegistry.get_or_create` 的
                速率（通常来自 `SystemConfig.rate_limits.tushare_per_min`）。
            tushare_retry_sleep: 转发给内部 `TushareSource` 的重试等待函数；
                单测注入 no-op，避免"单票拉取失败→tenacity 重试"路径引入真实
                sleep（与 `TushareSource` 自身单测同一惯例）。
        """
        self._codes = tuple(codes)
        self._initial_start_date = initial_start_date
        self._registry = rate_limiter_registry
        self._tushare_rate_per_min = tushare_rate_per_min
        self._tushare_retry_sleep = tushare_retry_sleep

    def sync(
        self,
        *,
        market: Market,
        as_of_date: date,
        transport: TushareTransport,
        repo: Repository,
    ) -> SyncSummary:
        """对名单内每只票做增量同步；单只票失败不阻塞其他票（结果记入
        `CodeSyncResult.error`，本次运行内跳过）。"""
        if not self._codes:
            return SyncSummary(market=market, as_of_date=as_of_date, results=())

        rate_limiter = self._registry.get_or_create(
            _TUSHARE_SOURCE_NAME, self._tushare_rate_per_min
        )
        source = TushareSource(
            rate_limiter=rate_limiter, transport=transport, retry_sleep=self._tushare_retry_sleep
        )

        results = tuple(
            self._sync_one_code(
                code, market=market, as_of_date=as_of_date, source=source, repo=repo
            )
            for code in self._codes
        )
        return SyncSummary(market=market, as_of_date=as_of_date, results=results)

    # ---- 单只票 ----------------------------------------------------------------

    def _sync_one_code(
        self,
        code: str,
        *,
        market: Market,
        as_of_date: date,
        source: TushareSource,
        repo: Repository,
    ) -> CodeSyncResult:
        try:
            return self._sync_one_code_unsafe(
                code, market=market, as_of_date=as_of_date, source=source, repo=repo
            )
        except _PartialSyncFailure as exc:
            # kline 已成功提交、valuation 提交失败：CodeSyncResult 必须如实反映
            # 已写入的 kline_rows（HIGH 修复——不得把已落库的行数报告成 0），
            # 该票仍判失败（error 不为空），下一次 sync 会通过
            # `_resolve_missing_start` 的自愈式续传起点重新补齐 valuation 缺口。
            return CodeSyncResult(
                code=code,
                kline_rows=exc.kline_rows,
                valuation_rows=exc.valuation_rows,
                error=str(exc.__cause__),
            )
        except Exception as exc:  # noqa: BLE001 - 单票失败不得中断整批同步，异常记录而非吞掉
            return CodeSyncResult(code=code, error=str(exc))

    def _sync_one_code_unsafe(
        self,
        code: str,
        *,
        market: Market,
        as_of_date: date,
        source: TushareSource,
        repo: Repository,
    ) -> CodeSyncResult:
        missing_start = self._resolve_missing_start(code, market=market, repo=repo)
        missing_days = trading_days_in_range(missing_start, as_of_date, market)
        if not missing_days:
            # no-op 语义（M4）：两种情况都会走到这里、都返回"0 行 + ok=True"——
            # (a) 正常路径：该票已同步到 as_of_date；(b) 异常调用形态：调用方传入
            # 的 as_of_date 早于 Repository 该票已有数据的最新日期（例如误传了
            # 过期的 as_of_date）。本函数不区分这两种情况，也不因为 (b) 而报错，
            # 调用方不应把"本次 0 行成功"直接解读为"数据已同步到 as_of_date"，
            # 应以 Repository 实际内容（`get_latest_kline_date`/
            # `get_latest_valuation_date`）为准。
            return CodeSyncResult(code=code)

        start_param = missing_days[0].strftime("%Y%m%d")
        end_param = missing_days[-1].strftime("%Y%m%d")

        kline_df = source.fetch_kline(codes=[code], start_date=start_param, end_date=end_param)
        kline_df = _clip_to_as_of(kline_df, as_of_date)

        valuation_df = source.fetch_daily_basic(
            codes=[code], start_date=start_param, end_date=end_param
        )
        valuation_df = _clip_to_as_of(valuation_df, as_of_date)
        valuation_df = _fill_turnover_amt(valuation_df, kline_df)

        kline_rows = repo.upsert_kline(kline_df)
        # kline_rows 之后：kline 已经提交（各自独立的 WriteQueue 事务，见模块
        # docstring）。若下面的 valuation 提交失败，必须把已经写入的 kline_rows
        # 带出去，而不能让异常从这里直接冒泡到外层通用 `except Exception`——
        # 那样会丢失 kline_rows 的真实值（HIGH 修复的核心）。
        try:
            valuation_rows = repo.upsert_valuation(valuation_df)
        except Exception as exc:
            raise _PartialSyncFailure(kline_rows=kline_rows, valuation_rows=0) from exc
        return CodeSyncResult(code=code, kline_rows=kline_rows, valuation_rows=valuation_rows)

    def _resolve_missing_start(self, code: str, *, market: Market, repo: Repository) -> date:
        """该票增量同步的起点（自愈式续传，HIGH 修复）：分别取 kline / valuation
        各自的最新日期，**取两者较小值**的下一交易日作为起点——任一张表落后，
        下一交易日起都会被重新拉取，覆盖那张表的缺口（`upsert_kline`/
        `upsert_valuation` 均为幂等 upsert，重复写入已有行不会有副作用）。

        两表中只要有一张对该票完全没有数据（`None`），说明该票要么从未同步过、
        要么此前发生过"kline 已落库但 valuation 从未成功写入过一次"的部分失败
        遗留——两种情况都退回 `initial_start_date` 整段重新拉取，是最安全的
        保守选择。
        """
        repo_code = _strip_exchange_suffix(code)
        latest_kline = repo.get_latest_kline_date(repo_code, market)
        latest_valuation = repo.get_latest_valuation_date(repo_code, market)
        if latest_kline is None or latest_valuation is None:
            return self._initial_start_date
        latest_common = min(latest_kline, latest_valuation)  # ISO 日期字符串可直接比较
        return next_trading_day(date.fromisoformat(latest_common), market)


class _PartialSyncFailure(Exception):
    """内部信号：kline 已提交、valuation 提交失败——用来把已写入的真实行数
    从 `_sync_one_code_unsafe` 带到 `_sync_one_code` 的异常处理分支，不参与
    对外接口（不出现在 `__all__`）。真正的失败原因保留在 `__cause__` 里。
    """

    def __init__(self, *, kline_rows: int, valuation_rows: int) -> None:
        super().__init__(f"valuation 提交失败，但 kline 已成功写入 {kline_rows} 行（未回滚）")
        self.kline_rows = kline_rows
        self.valuation_rows = valuation_rows


__all__ = ["CodeSyncResult", "SyncSummary", "SyncEngine"]
