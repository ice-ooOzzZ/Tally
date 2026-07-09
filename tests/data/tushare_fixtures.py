"""Tushare 录制回放的测试支持代码（非 `test_*.py`，不被 pytest 当作测试文件收集；
用法与 `tests/synth/generators.py` 同一惯例：辅助模块与其单测放在同一目录）。

- `ReplayTushareTransport`：从 `tests/fixtures/tushare/*.json` 加载手工构造/录制的
  响应，实现与 `tally.data.sources.tushare_source.TushareTransport` 相同的接口，
  按 `ts_code`/`trade_date`/`start_date`/`end_date` 过滤——语义对齐真实
  `pro_api`（各过滤条件均可省略；给了就精确/区间匹配）。单测用它替代真实网络调用。
- `RecordingTushareTransport`：包一层任意 `TushareTransport`（通常是真实
  `ts.pro_api(token)`），透传调用的同时把原始返回记录下来，`save()` 写回
  `tests/fixtures/tushare/*.json`，格式与本目录现有 fixture 完全一致，可直接
  覆盖替换手工样本。录制需要真实 `TUSHARE_TOKEN` 与已安装的 `tushare` 包，
  本仓库当前开发环境两者都不具备，故只在此提供机制、附文档说明用法
  （见 `tests/fixtures/tushare/README.md`），不在 CI 中执行。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tushare"

_ENDPOINT_FILES = {
    "daily": "daily.json",
    "adj_factor": "adj_factor.json",
    "daily_basic": "daily_basic.json",
}


def _load_fixture(endpoint: str, fixtures_dir: Path) -> list[dict[str, Any]]:
    path = fixtures_dir / _ENDPOINT_FILES[endpoint]
    if not path.is_file():
        raise FileNotFoundError(f"缺少 fixture 文件：{path}")
    raw: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return raw


def _matches(
    record: dict[str, Any],
    *,
    ts_code: str,
    trade_date: str,
    start_date: str,
    end_date: str,
) -> bool:
    if ts_code and record.get("ts_code") != ts_code:
        return False
    if trade_date and record.get("trade_date") != trade_date:
        return False
    record_date = str(record.get("trade_date", ""))
    if start_date and record_date < start_date:
        return False
    if end_date and record_date > end_date:
        return False
    return True


class ReplayTushareTransport:
    """离线回放 fixture 的 `TushareTransport` 实现（结构性满足 Protocol，无需继承）。"""

    def __init__(self, fixtures_dir: Path | None = None) -> None:
        self._fixtures_dir = fixtures_dir or _FIXTURES_DIR
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def _records(self, endpoint: str) -> list[dict[str, Any]]:
        if endpoint not in self._cache:
            self._cache[endpoint] = _load_fixture(endpoint, self._fixtures_dir)
        return self._cache[endpoint]

    def _call(
        self,
        endpoint: str,
        *,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        matched = [
            record
            for record in self._records(endpoint)
            if _matches(
                record,
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
            )
        ]
        return pd.DataFrame(matched)

    def daily(
        self,
        *,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        return self._call(
            "daily",
            ts_code=ts_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
        )

    def adj_factor(
        self,
        *,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        return self._call(
            "adj_factor",
            ts_code=ts_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
        )

    def daily_basic(
        self,
        *,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        return self._call(
            "daily_basic",
            ts_code=ts_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
        )


class RecordingTushareTransport:
    """透传真实 transport 的调用并把原始响应记下来，供 `save()` 写成 fixture。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._recorded: dict[str, list[dict[str, Any]]] = {name: [] for name in _ENDPOINT_FILES}

    def _record_call(self, endpoint: str, **kwargs: str) -> pd.DataFrame:
        result: pd.DataFrame = getattr(self._inner, endpoint)(**kwargs)
        # `to_dict(orient="records")` 的 pandas-stubs 返回类型是
        # list[dict[Hashable, Any]]（列名的静态类型是 Hashable，而非 str）；
        # 运行时列名始终是 str（本模块自己构造的 DataFrame 或真实 Tushare 响应皆然），
        # 与 tally/data/_rows.py 里的同类窄化标注一致。
        records: list[dict[str, Any]] = result.to_dict(orient="records")  # type: ignore[assignment]
        self._recorded[endpoint].extend(records)
        return result

    def daily(self, **kwargs: str) -> pd.DataFrame:
        return self._record_call("daily", **kwargs)

    def adj_factor(self, **kwargs: str) -> pd.DataFrame:
        return self._record_call("adj_factor", **kwargs)

    def daily_basic(self, **kwargs: str) -> pd.DataFrame:
        return self._record_call("daily_basic", **kwargs)

    def save(self, out_dir: Path) -> None:
        """把已录制的原始记录写回 `out_dir` 下对应的 `<endpoint>.json`（覆盖写入）。"""
        out_dir.mkdir(parents=True, exist_ok=True)
        for endpoint, filename in _ENDPOINT_FILES.items():
            records = self._recorded[endpoint]
            if not records:
                continue
            (out_dir / filename).write_text(
                json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
            )
