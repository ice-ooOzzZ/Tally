"""Tushare 适配器（IMPLEMENTATION_SPEC.md §3.1 A股 #1–3：`daily`/`adj_factor`/
`daily_basic`）。

范围与边界（M1 T1.2）：
- **只拉数 + 归一化成 DataFrame，不触碰 Repository**——落库是 T1.3 `sync.py` 的事；
  本模块任何方法都不 import `tally.data.repository`。
- 输出列名对齐 `schema.sql` 的 `kline`/`valuation` 表；`daily_basic` 接口本身不
  提供成交额字段（成交额来自 `daily.amount`），故本模块归一化后的
  `turnover_amt` 列恒为 NaN，由 T1.3 同步层从 `fetch_daily` 的结果关联填充。
- **令牌桶 + 重试**：每次真实调用先过 `TokenBucket.acquire()`，再套
  `tenacity.Retrying` 做指数退避重试；重试次数/退避参数是工程实现细节（不是
  业务可调参数），故用命名常量而非 `config/*.yaml`（先例：`repository.py` 的
  `_BUSY_TIMEOUT_MS`）。
- **依赖注入**：真正的网络客户端（`tushare.pro_api`）通过 `transport` 构造参数
  注入；单测一律注入 fixture 回放的假 transport（见
  `tests/data/tushare_fixtures.py::ReplayTushareTransport`），本模块任何单测
  路径都不会 `import tushare`、不会请求网络、不需要 `TUSHARE_TOKEN`。
- **缺失密钥 loud failure**：只有在没有注入 `transport` 且真正发起一次调用时，
  才检查 `TUSHARE_TOKEN`——为空则抛 `TushareAuthError`，消息明确指出如何配置；
  导入本模块、构造 `TushareSource` 实例本身都不要求 token。
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from typing import Protocol

import pandas as pd
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from tally.data.rate_limit import Sleep, TokenBucket

_TOKEN_ENV_VAR = "TUSHARE_TOKEN"
_MARKET_CN = "CN"
_SOURCE_PRIMARY = "primary"

# 重试是"瞬时故障容错"的工程实现细节，不是业务可调参数（不涉及策略阈值/窗口等
# 业务语义），因此用命名常量而非 config/*.yaml；CLAUDE.md"无魔法数字"约束的是
# 业务参数不得裸写，工程常量走命名常量即满足要求（同类先例：repository.py 的
# `_BUSY_TIMEOUT_MS`）。
_DEFAULT_RETRY_ATTEMPTS = 3
_DEFAULT_RETRY_WAIT_MIN_S = 1.0
_DEFAULT_RETRY_WAIT_MAX_S = 8.0

# Tushare 接口的固定单位换算（接口契约本身决定，不是可调业务参数）：
# `daily.amount` 单位是千元；`daily_basic.total_mv`/`circ_mv` 单位是万元。
_AMOUNT_UNIT_TO_YUAN = 1_000.0
_TOTAL_MV_UNIT_TO_YUAN = 10_000.0

_KLINE_DAILY_COLUMNS = (
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
)
_ADJ_FACTOR_COLUMNS = ("code", "market", "date", "adj_factor")
_VALUATION_COLUMNS = ("code", "market", "date", "pe_ttm", "pb", "market_cap", "turnover_amt")
_KLINE_FULL_COLUMNS = (
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
)


class TushareAuthError(RuntimeError):
    """`TUSHARE_TOKEN` 缺失/为空（或缺 `tushare` 包）时的显式失败。

    设计意图：绝不允许把空字符串静默传给真实 API 再让 Tushare 返回一个模糊的
    远端错误——在本地就能判断的配置问题，必须在本地就给出清晰、可操作的报错。
    """


class TransientTushareError(RuntimeError):
    """转输层判定为可重试的瞬时故障（网络错误/服务端限流/连接超时等）。

    转输层调用中抛出的任何非 `TushareAuthError` 异常都会在 `_call_once` 里被
    统一包装为本类型再交给 `tenacity` 判断是否重试；重试到达上限后仍失败时，
    原始异常通过 `raise ... from exc` 保留在 `__cause__` 链上，不会被吞掉。
    """


class TushareTransport(Protocol):
    """`tushare.pro_api(token)` 返回对象需要满足的最小接口（结构化子类型）。

    真实 SDK 的这三个方法均接受空字符串表示"不按该字段过滤"（而非 `None`），
    本 Protocol 的默认值与真实签名保持一致，便于 `TushareSource` 与真实/假
    transport 之间零转换直连。
    """

    def daily(
        self, *, ts_code: str = "", trade_date: str = "", start_date: str = "", end_date: str = ""
    ) -> pd.DataFrame: ...

    def adj_factor(
        self, *, ts_code: str = "", trade_date: str = "", start_date: str = "", end_date: str = ""
    ) -> pd.DataFrame: ...

    def daily_basic(
        self, *, ts_code: str = "", trade_date: str = "", start_date: str = "", end_date: str = ""
    ) -> pd.DataFrame: ...


def _strip_ts_suffix(ts_code: str) -> str:
    """`"600000.SH"` → `"600000"`：Repository 与 config 里的 code 一律不带交易所后缀。"""
    return str(ts_code).split(".", 1)[0]


def _trade_date_to_iso(trade_date: str) -> str:
    """`"20240102"` → `"2024-01-02"`：对齐 schema.sql `date` 列的 `YYYY-MM-DD` 约定。"""
    text = str(trade_date)
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"trade_date 应为 YYYYMMDD 八位数字字符串，实际为 {trade_date!r}")
    return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"


def _normalize_daily(raw: pd.DataFrame) -> pd.DataFrame:
    """Tushare `daily` 原始响应 → kline 表列子集（不含 `adj_factor`）。"""
    if raw.empty:
        return pd.DataFrame(columns=_KLINE_DAILY_COLUMNS)
    out = pd.DataFrame(
        {
            "code": raw["ts_code"].map(_strip_ts_suffix),
            "market": _MARKET_CN,
            "date": raw["trade_date"].map(_trade_date_to_iso),
            "open": raw["open"].astype(float),
            "high": raw["high"].astype(float),
            "low": raw["low"].astype(float),
            "close": raw["close"].astype(float),
            "volume": raw["vol"].astype(float),
            "amount": raw["amount"].astype(float) * _AMOUNT_UNIT_TO_YUAN,
            "source": _SOURCE_PRIMARY,
        }
    )
    return out[list(_KLINE_DAILY_COLUMNS)]


def _normalize_adj_factor(raw: pd.DataFrame) -> pd.DataFrame:
    """Tushare `adj_factor` 原始响应 → `(code, market, date, adj_factor)`。"""
    if raw.empty:
        return pd.DataFrame(columns=_ADJ_FACTOR_COLUMNS)
    out = pd.DataFrame(
        {
            "code": raw["ts_code"].map(_strip_ts_suffix),
            "market": _MARKET_CN,
            "date": raw["trade_date"].map(_trade_date_to_iso),
            "adj_factor": raw["adj_factor"].astype(float),
        }
    )
    return out[list(_ADJ_FACTOR_COLUMNS)]


def _normalize_daily_basic(raw: pd.DataFrame) -> pd.DataFrame:
    """Tushare `daily_basic` 原始响应 → valuation 表列（`turnover_amt` 恒 NaN，见模块 docstring）"""
    if raw.empty:
        return pd.DataFrame(columns=_VALUATION_COLUMNS)
    out = pd.DataFrame(
        {
            "code": raw["ts_code"].map(_strip_ts_suffix),
            "market": _MARKET_CN,
            "date": raw["trade_date"].map(_trade_date_to_iso),
            "pe_ttm": raw["pe_ttm"].astype(float),
            "pb": raw["pb"].astype(float),
            "market_cap": raw["total_mv"].astype(float) * _TOTAL_MV_UNIT_TO_YUAN,
            "turnover_amt": float("nan"),
        }
    )
    return out[list(_VALUATION_COLUMNS)]


def merge_daily_and_adj_factor(daily_df: pd.DataFrame, adj_df: pd.DataFrame) -> pd.DataFrame:
    """把 `fetch_daily`/`fetch_adj_factor` 的归一化结果按 `(code, market, date)` 左连接，
    产出与 kline 表列完全对齐（含 `adj_factor`）的 DataFrame。

    左连接以行情（`daily_df`）为准：某日缺 `adj_factor`（如新股上市首日尚未有
    复权因子记录）时该行 `adj_factor` 落为 NaN，而不是整行被静默丢弃。
    """
    if daily_df.empty:
        return pd.DataFrame(columns=_KLINE_FULL_COLUMNS)
    merged = daily_df.merge(adj_df, on=["code", "market", "date"], how="left")
    return merged[list(_KLINE_FULL_COLUMNS)]


class TushareSource:
    """Tushare Pro 适配器：`daily`/`adj_factor`/`daily_basic` → 归一化 DataFrame。"""

    def __init__(
        self,
        *,
        rate_limiter: TokenBucket,
        token: str | None = None,
        transport: TushareTransport | None = None,
        retry_attempts: int = _DEFAULT_RETRY_ATTEMPTS,
        retry_wait_min_s: float = _DEFAULT_RETRY_WAIT_MIN_S,
        retry_wait_max_s: float = _DEFAULT_RETRY_WAIT_MAX_S,
        retry_sleep: Sleep = time.sleep,
    ) -> None:
        """
        Args:
            rate_limiter: 共享的令牌桶（一般来自 `RateLimiterRegistry.get_or_create(
                "tushare", config.rate_limits.tushare_per_min)`）；本类不自行创建，
                以便同进程内多个调用方共享同一份限流状态（§3.3"全局令牌桶"）。
            token: 显式传入的 Tushare token；为 `None` 时回退读取环境变量
                `TUSHARE_TOKEN`（不主动加载 `.env`——生产路径应由调用方在装配
                `SystemConfig` 时已解析好 `env:TUSHARE_TOKEN` 并显式传入，
                保持本模块与 `common/config` 解耦）。传入 `transport` 时
                token 是否存在完全不影响行为。
            transport: 注入的转输层；非 `None` 时永远优先使用，且完全不检查/
                不需要 token（回放测试路径）。为 `None` 时真正发起调用才会
                惰性构建真实 `tushare.pro_api(token)` 客户端。
            retry_sleep: 重试之间的等待函数；测试注入 no-op 以避免真实 sleep。
        """
        self._rate_limiter = rate_limiter
        self._token = token if token is not None else os.environ.get(_TOKEN_ENV_VAR, "")
        self._injected_transport = transport
        self._real_transport: TushareTransport | None = None
        self._retry_attempts = retry_attempts
        self._retry_wait_min_s = retry_wait_min_s
        self._retry_wait_max_s = retry_wait_max_s
        self._retry_sleep = retry_sleep

    # ---- 转输层解析（loud failure 落地点） -------------------------------------

    def _resolve_transport(self) -> TushareTransport:
        if self._injected_transport is not None:
            return self._injected_transport
        if self._real_transport is not None:
            return self._real_transport

        if not self._token:
            raise TushareAuthError(
                "TUSHARE_TOKEN 未设置或为空：请在项目根目录 .env 中配置 "
                "TUSHARE_TOKEN=<你的 Tushare Pro token> 后重试。"
                "（回放测试应注入 fake transport，不应走到这条真实调用路径。）"
            )
        try:
            import tushare as ts  # 延迟导入：模块级 import 不得要求已安装 tushare
        except ImportError as exc:
            raise TushareAuthError(
                "未安装 tushare 包：请 `uv sync --extra data` 后重试"
                "（生产/录制模式需要真实 SDK；回放测试应注入 fake transport）。"
            ) from exc

        self._real_transport = ts.pro_api(self._token)
        return self._real_transport

    # ---- 单次调用：限流 + 异常归类 ------------------------------------------------

    def _call_once(
        self,
        transport: TushareTransport,
        endpoint: str,
        *,
        ts_code: str,
        trade_date: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        self._rate_limiter.acquire(1.0)
        method = getattr(transport, endpoint)
        try:
            result: pd.DataFrame = method(
                ts_code=ts_code, trade_date=trade_date, start_date=start_date, end_date=end_date
            )
        except TransientTushareError:
            raise
        except Exception as exc:
            # 转输层的任何异常都归类为潜在瞬时故障纳入重试（网络抖动/服务端限流等
            # 场景占绝大多数）；若确实是不可恢复错误，重试只会在到达上限后原样
            # 暴露（`raise ... from exc` 保留原始异常在 __cause__ 链上），不会被吞掉。
            raise TransientTushareError(f"Tushare {endpoint!r} 调用失败：{exc}") from exc
        return result

    def _call_with_retry(
        self,
        transport: TushareTransport,
        endpoint: str,
        *,
        ts_code: str,
        trade_date: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        retrying: Retrying = Retrying(
            stop=stop_after_attempt(self._retry_attempts),
            wait=wait_exponential(
                multiplier=1, min=self._retry_wait_min_s, max=self._retry_wait_max_s
            ),
            retry=retry_if_exception_type(TransientTushareError),
            sleep=self._retry_sleep,
            reraise=True,
        )
        result: pd.DataFrame = retrying(
            self._call_once,
            transport,
            endpoint,
            ts_code=ts_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
        )
        return result

    def _fetch_raw(
        self,
        endpoint: str,
        *,
        trade_date: str | None,
        codes: Sequence[str] | None,
        start_date: str | None,
        end_date: str | None,
    ) -> pd.DataFrame:
        transport = self._resolve_transport()
        code_list: list[str | None] = list(codes) if codes else [None]
        frames = [
            self._call_with_retry(
                transport,
                endpoint,
                ts_code=code or "",
                trade_date=trade_date or "",
                start_date=start_date or "",
                end_date=end_date or "",
            )
            for code in code_list
        ]
        non_empty = [frame for frame in frames if not frame.empty]
        if not non_empty:
            return frames[0] if frames else pd.DataFrame()
        return pd.concat(non_empty, ignore_index=True)

    # ---- 公开接口：三个数据源 + 一个组合便捷方法 ----------------------------------

    def fetch_daily(
        self,
        *,
        trade_date: str | None = None,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """§3.1 #1：全市场日频行情（`trade_date` 批量）或 §3.1 #2：单/多票日K（`codes` + 区间）。"""
        raw = self._fetch_raw(
            "daily", trade_date=trade_date, codes=codes, start_date=start_date, end_date=end_date
        )
        return _normalize_daily(raw)

    def fetch_adj_factor(
        self,
        *,
        trade_date: str | None = None,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """§3.1 #2：复权因子。"""
        raw = self._fetch_raw(
            "adj_factor",
            trade_date=trade_date,
            codes=codes,
            start_date=start_date,
            end_date=end_date,
        )
        return _normalize_adj_factor(raw)

    def fetch_daily_basic(
        self,
        *,
        trade_date: str | None = None,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """§3.1 #3：日频估值（`pe_ttm`/`pb`/市值）。"""
        raw = self._fetch_raw(
            "daily_basic",
            trade_date=trade_date,
            codes=codes,
            start_date=start_date,
            end_date=end_date,
        )
        return _normalize_daily_basic(raw)

    def fetch_kline(
        self,
        *,
        trade_date: str | None = None,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """`fetch_daily` + `fetch_adj_factor` 合并为完整 kline 表列（含 `adj_factor`）。

        便捷方法：§3.1 #2 原文把"池内日K+复权因子"描述为一件事（Tushare `daily` +
        `adj_factor` 两个接口配合使用），T1.3 同步层落库前需要的正是这份合并结果。
        """
        daily_df = self.fetch_daily(
            trade_date=trade_date, codes=codes, start_date=start_date, end_date=end_date
        )
        adj_df = self.fetch_adj_factor(
            trade_date=trade_date, codes=codes, start_date=start_date, end_date=end_date
        )
        return merge_daily_and_adj_factor(daily_df, adj_df)


__all__ = [
    "TushareAuthError",
    "TransientTushareError",
    "TushareTransport",
    "TushareSource",
    "merge_daily_and_adj_factor",
]
