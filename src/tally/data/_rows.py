"""kline / valuation 写入前的行归一化：`DataFrame` 或字典序列 → 补全默认值的
`list[dict]`（供 `executemany` 的命名占位符使用）。

纯工程基建（数据整形），不含业务参数，故未走 `config/*.yaml`。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd

_KLINE_COLUMNS = (
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
_KLINE_REQUIRED = ("code", "market", "date")
_KLINE_DEFAULTS: dict[str, Any] = {"source": "primary"}

_VALUATION_COLUMNS = ("code", "market", "date", "pe_ttm", "pb", "market_cap", "turnover_amt")
_VALUATION_REQUIRED = ("code", "market", "date")


def _to_records(rows: pd.DataFrame | Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if isinstance(rows, pd.DataFrame):
        # DataFrame.to_dict(orient="records") 的 pandas-stubs 返回类型是
        # list[dict[Hashable, Any]]（列名的静态类型是 Hashable，而非 str）；
        # 本仓库的列名在运行时始终是 str，这里按调用方契约窄化标注。
        records: list[Mapping[str, Any]] = rows.to_dict(orient="records")  # type: ignore[assignment]
        return records
    return list(rows)


def _is_missing(value: Any) -> bool:
    """None，或 pandas 语义下的"缺失"（NaN/NaT/`pd.NA`）都算缺失。

    `DataFrame.to_dict(orient="records")` 对缺失单元格填的是 `float('nan')`
    而非 `None`；用 `pd.isna` 而非手写 `is None`/`math.isnan`，一次性覆盖
    NaN/NaT/`pd.NA` 等变体，不遗漏。
    """
    return bool(pd.isna(value))


def _normalize(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    columns: tuple[str, ...],
    required: tuple[str, ...],
    defaults: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    resolved_defaults = defaults or {}
    normalized: list[dict[str, Any]] = []
    for raw in _to_records(rows):
        missing = [col for col in required if _is_missing(raw.get(col))]
        if missing:
            raise ValueError(f"写入行缺少必填字段 {missing}：{raw!r}")

        record: dict[str, Any] = {}
        for col in columns:
            value = raw.get(col)
            # 字段存在但显式为 None/NaN 时也要落到默认值——`dict.get(key, default)`
            # 只在 key 缺失时才生效，覆盖不到"显式 None"这种很常见的输入形态
            # （例如 JSON 里的 null，或 DataFrame 某列部分为 NaN）。
            if _is_missing(value):
                value = resolved_defaults.get(col)
            record[col] = value
        normalized.append(record)
    return normalized


def normalize_kline_rows(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """归一化待写入 kline 的行：补全 `source` 默认值 `'primary'`，校验 PK 三列必填。"""
    return _normalize(rows, _KLINE_COLUMNS, _KLINE_REQUIRED, _KLINE_DEFAULTS)


def normalize_valuation_rows(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """归一化待写入 valuation 的行：校验 PK 三列必填。"""
    return _normalize(rows, _VALUATION_COLUMNS, _VALUATION_REQUIRED)
