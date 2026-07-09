"""`_rows.py` 归一化单测：显式 None 落默认值、pandas NaN 视为缺失。

这两条是代码审查发现的真实 bug 的回归用例：
1. `dict.get(key, default)` 只在 key 缺失时才用默认值，字段存在但显式为
   `None`/NaN 时不会——之前会把 `None`/NaN 原样写进 DB，撞上 `NOT NULL` 约束。
2. `DataFrame.to_dict(orient="records")` 对缺失单元格填 `float('nan')` 而非
   `None`，之前的必填字段校验（`is None`）漏判，等到写库时才炸出一个不友好的
   `sqlite3.IntegrityError`，而不是这里应该给出的清晰 `ValueError`。
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from tally.data._rows import normalize_kline_rows, normalize_valuation_rows

# ---- 显式 None / NaN 落默认值 -----------------------------------------------------


def test_explicit_none_for_defaulted_column_falls_back_to_default() -> None:
    rows = normalize_kline_rows(
        [
            {
                "code": "600000",
                "market": "CN",
                "date": "2024-01-02",
                "source": None,  # 显式 None，而非"没传这个 key"
            }
        ]
    )
    assert rows[0]["source"] == "primary"


def test_nan_for_defaulted_column_falls_back_to_default() -> None:
    df = pd.DataFrame(
        [
            {
                "code": "600000",
                "market": "CN",
                "date": "2024-01-02",
                "close": 10.0,
                "source": float("nan"),
            }
        ]
    )
    rows = normalize_kline_rows(df)
    assert rows[0]["source"] == "primary"


def test_missing_key_for_defaulted_column_still_falls_back_to_default() -> None:
    rows = normalize_kline_rows([{"code": "600000", "market": "CN", "date": "2024-01-02"}])
    assert rows[0]["source"] == "primary"


def test_nan_for_non_defaulted_optional_column_normalizes_to_none_not_nan() -> None:
    df = pd.DataFrame(
        [{"code": "600000", "market": "CN", "date": "2024-01-02", "close": float("nan")}]
    )
    rows = normalize_kline_rows(df)
    assert rows[0]["close"] is None  # 不应残留 NaN 字面量流进 SQL 绑定参数


# ---- 必填字段缺失：None 与 NaN 都要触发同一条 ValueError -----------------------------


def test_dict_row_missing_required_field_raises() -> None:
    with pytest.raises(ValueError, match="缺少必填字段"):
        normalize_kline_rows([{"code": "600000", "market": "CN", "close": 10.0}])  # 无 date


def test_dict_row_with_explicit_none_required_field_raises() -> None:
    with pytest.raises(ValueError, match="缺少必填字段"):
        normalize_kline_rows([{"code": "600000", "market": "CN", "date": None}])


def test_dict_row_with_nan_required_field_raises() -> None:
    with pytest.raises(ValueError, match="缺少必填字段"):
        normalize_kline_rows([{"code": "600000", "market": "CN", "date": math.nan}])


def test_dataframe_row_missing_required_field_raises_not_silently_passes() -> None:
    # 混合字典拼出的 DataFrame：某一行缺 code 列，pandas 用 NaN 补齐（不是 None）。
    df = pd.DataFrame(
        [
            {"code": "600000", "market": "CN", "date": "2024-01-02", "close": 10.0},
            {"market": "CN", "date": "2024-01-03", "close": 11.0},  # 缺 code
        ]
    )
    with pytest.raises(ValueError, match="缺少必填字段"):
        normalize_kline_rows(df)


def test_valuation_dataframe_row_missing_required_field_raises() -> None:
    df = pd.DataFrame([{"code": "600000", "market": "CN", "date": "2024-01-02", "pe_ttm": 10.0}])
    df.loc[0, "code"] = float("nan")
    with pytest.raises(ValueError, match="缺少必填字段"):
        normalize_valuation_rows(df)
