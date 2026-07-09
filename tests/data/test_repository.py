"""M1 T1.1 单测：kline/valuation/signals 三表 CRUD、upsert 覆盖语义、
market 隔离、建表幂等。

对应 IMPLEMENTATION_SPEC.md §11 T1.1；并发压测另见
`tests/data/test_repository_concurrency.py`。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tally.data.repository import Repository


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "tally.db"


def _kline_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "code": "600000",
        "market": "CN",
        "date": "2024-01-02",
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.2,
        "volume": 1_000_000.0,
        "amount": 10_200_000.0,
        "adj_factor": 1.0,
    }
    row.update(overrides)
    return row


def _valuation_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "code": "600000",
        "market": "CN",
        "date": "2024-01-02",
        "pe_ttm": 15.3,
        "pb": 1.8,
        "market_cap": 5.0e10,
        "turnover_amt": 1.0e8,
    }
    row.update(overrides)
    return row


# ---- kline ---------------------------------------------------------------------


def test_upsert_kline_then_get_kline_roundtrip(db_path: Path) -> None:
    with Repository(db_path) as repo:
        written = repo.upsert_kline([_kline_row()])
        assert written == 1

        df = repo.get_kline("600000", "CN")
        assert len(df) == 1
        assert df.iloc[0]["close"] == pytest.approx(10.2)
        assert df.iloc[0]["source"] == "primary"  # 未显式指定时的默认值


def test_upsert_kline_conflict_updates_in_place_not_duplicate(db_path: Path) -> None:
    with Repository(db_path) as repo:
        repo.upsert_kline([_kline_row(close=10.2)])
        repo.upsert_kline([_kline_row(close=11.5, volume=2_000_000.0)])  # 同 PK 再写一次

        df = repo.get_kline("600000", "CN")
        assert len(df) == 1  # ON CONFLICT DO UPDATE：整行覆盖而非追加
        assert df.iloc[0]["close"] == pytest.approx(11.5)
        assert df.iloc[0]["volume"] == pytest.approx(2_000_000.0)


def test_upsert_kline_accepts_dataframe(db_path: Path) -> None:
    with Repository(db_path) as repo:
        df_in = pd.DataFrame([_kline_row(date="2024-01-03"), _kline_row(date="2024-01-04")])
        written = repo.upsert_kline(df_in)
        assert written == 2

        df_out = repo.get_kline("600000", "CN")
        assert list(df_out["date"]) == ["2024-01-03", "2024-01-04"]


def test_upsert_kline_explicit_source_overrides_default(db_path: Path) -> None:
    with Repository(db_path) as repo:
        repo.upsert_kline([_kline_row(source="stooq")])
        df = repo.get_kline("600000", "CN")
        assert df.iloc[0]["source"] == "stooq"


def test_upsert_kline_missing_required_field_raises(db_path: Path) -> None:
    with Repository(db_path) as repo:
        bad_row = _kline_row()
        del bad_row["date"]
        with pytest.raises(ValueError, match="缺少必填字段"):
            repo.upsert_kline([bad_row])


def test_upsert_kline_empty_input_is_noop(db_path: Path) -> None:
    with Repository(db_path) as repo:
        assert repo.upsert_kline([]) == 0
        assert repo.get_kline("600000", "CN").empty


def test_get_kline_date_range_filters_inclusive(db_path: Path) -> None:
    with Repository(db_path) as repo:
        rows = [_kline_row(date=f"2024-01-{d:02d}") for d in range(1, 6)]
        repo.upsert_kline(rows)

        df = repo.get_kline("600000", "CN", start="2024-01-02", end="2024-01-04")
        assert list(df["date"]) == ["2024-01-02", "2024-01-03", "2024-01-04"]


def test_get_kline_only_start_returns_everything_from_start_onward(db_path: Path) -> None:
    """只给 `start`（不给 `end`）：应取到 start（含）之后的所有行，而非因为
    `end` 缺省就意外把整段查询裁掉或报错——补测单边范围查询。"""
    with Repository(db_path) as repo:
        rows = [_kline_row(date=f"2024-01-{d:02d}") for d in range(1, 6)]
        repo.upsert_kline(rows)

        df = repo.get_kline("600000", "CN", start="2024-01-03")
        assert list(df["date"]) == ["2024-01-03", "2024-01-04", "2024-01-05"]


def test_get_kline_only_end_returns_everything_up_to_end(db_path: Path) -> None:
    """只给 `end`（不给 `start`）：应取到 end（含）之前的所有行。"""
    with Repository(db_path) as repo:
        rows = [_kline_row(date=f"2024-01-{d:02d}") for d in range(1, 6)]
        repo.upsert_kline(rows)

        df = repo.get_kline("600000", "CN", end="2024-01-03")
        assert list(df["date"]) == ["2024-01-01", "2024-01-02", "2024-01-03"]


def test_get_kline_no_match_returns_empty_dataframe_with_correct_columns(db_path: Path) -> None:
    """查无结果时也要返回列名正确的空 DataFrame（而非空列表/None），
    调用方无需为"零行"和"有行"两种形态分别写不同的列访问逻辑。"""
    with Repository(db_path) as repo:
        df = repo.get_kline("NOSUCH", "CN")
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


# ---- valuation -------------------------------------------------------------------


def test_upsert_valuation_then_get_valuation_roundtrip(db_path: Path) -> None:
    with Repository(db_path) as repo:
        written = repo.upsert_valuation([_valuation_row()])
        assert written == 1

        df = repo.get_valuation("600000", "CN")
        assert len(df) == 1
        assert df.iloc[0]["pe_ttm"] == pytest.approx(15.3)


def test_upsert_valuation_conflict_updates_in_place(db_path: Path) -> None:
    with Repository(db_path) as repo:
        repo.upsert_valuation([_valuation_row(pe_ttm=15.3)])
        repo.upsert_valuation([_valuation_row(pe_ttm=16.1)])

        df = repo.get_valuation("600000", "CN")
        assert len(df) == 1
        assert df.iloc[0]["pe_ttm"] == pytest.approx(16.1)


def test_upsert_valuation_empty_input_is_noop(db_path: Path) -> None:
    with Repository(db_path) as repo:
        assert repo.upsert_valuation([]) == 0
        assert repo.get_valuation("600000", "CN").empty


def test_upsert_valuation_missing_required_field_raises(db_path: Path) -> None:
    with Repository(db_path) as repo:
        bad_row = _valuation_row()
        del bad_row["code"]
        with pytest.raises(ValueError, match="缺少必填字段"):
            repo.upsert_valuation([bad_row])


def test_get_valuation_date_range_filters_inclusive(db_path: Path) -> None:
    with Repository(db_path) as repo:
        rows = [_valuation_row(date=f"2024-01-{d:02d}") for d in range(1, 6)]
        repo.upsert_valuation(rows)

        df = repo.get_valuation("600000", "CN", start="2024-01-02", end="2024-01-04")
        assert list(df["date"]) == ["2024-01-02", "2024-01-03", "2024-01-04"]


def test_get_valuation_only_start_returns_everything_from_start_onward(db_path: Path) -> None:
    """补测单边范围查询（只给 start）：与 `get_kline` 的同类回归对称。"""
    with Repository(db_path) as repo:
        rows = [_valuation_row(date=f"2024-01-{d:02d}") for d in range(1, 6)]
        repo.upsert_valuation(rows)

        df = repo.get_valuation("600000", "CN", start="2024-01-03")
        assert list(df["date"]) == ["2024-01-03", "2024-01-04", "2024-01-05"]


def test_get_valuation_only_end_returns_everything_up_to_end(db_path: Path) -> None:
    """补测单边范围查询（只给 end）：与 `get_kline` 的同类回归对称。"""
    with Repository(db_path) as repo:
        rows = [_valuation_row(date=f"2024-01-{d:02d}") for d in range(1, 6)]
        repo.upsert_valuation(rows)

        df = repo.get_valuation("600000", "CN", end="2024-01-03")
        assert list(df["date"]) == ["2024-01-01", "2024-01-02", "2024-01-03"]


def test_get_valuation_no_match_returns_empty_dataframe_with_correct_columns(
    db_path: Path,
) -> None:
    """查无结果时返回空 DataFrame 且列名正确（补测缺口）。"""
    with Repository(db_path) as repo:
        df = repo.get_valuation("NOSUCH", "CN")
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


# ---- signals ---------------------------------------------------------------------


def test_insert_signal_returns_incrementing_id(db_path: Path) -> None:
    with Repository(db_path) as repo:
        id1 = repo.insert_signal(
            strategy_id="s1", date="2024-01-02", code="600000", market="CN", advice="buy"
        )
        id2 = repo.insert_signal(
            strategy_id="s1", date="2024-01-02", code="600001", market="CN", advice="buy"
        )
        assert id2 == id1 + 1


def test_get_signals_filters_by_market_and_date(db_path: Path) -> None:
    with Repository(db_path) as repo:
        repo.insert_signal(
            strategy_id="s1", date="2024-01-02", code="600000", market="CN", advice="buy"
        )
        repo.insert_signal(
            strategy_id="s1", date="2024-01-03", code="600000", market="CN", advice="exit"
        )
        repo.insert_signal(
            strategy_id="s1", date="2024-01-02", code="AAPL", market="US", advice="buy"
        )

        df = repo.get_signals("CN", "2024-01-02")
        assert len(df) == 1
        assert df.iloc[0]["code"] == "600000"
        assert df.iloc[0]["advice"] == "buy"


def test_get_signals_no_match_returns_empty_dataframe_with_correct_columns(db_path: Path) -> None:
    """查无结果时返回空 DataFrame 且列名正确（补测缺口）。"""
    with Repository(db_path) as repo:
        df = repo.get_signals("CN", "2099-01-01")
        assert df.empty
        assert list(df.columns) == [
            "id",
            "strategy_id",
            "date",
            "code",
            "market",
            "advice",
            "score",
            "reasons_json",
            "price_at_signal",
            "stop_loss",
            "position_pct",
        ]


def test_signal_optional_fields_roundtrip(db_path: Path) -> None:
    with Repository(db_path) as repo:
        signal_id = repo.insert_signal(
            strategy_id="s1",
            date="2024-01-02",
            code="600000",
            market="CN",
            advice="buy",
            score=0.87,
            reasons_json='["breakout"]',
            price_at_signal=10.2,
            stop_loss=9.4,
            position_pct=0.08,
        )
        df = repo.get_signals("CN", "2024-01-02")
        row = df[df["id"] == signal_id].iloc[0]
        assert row["score"] == pytest.approx(0.87)
        assert row["reasons_json"] == '["breakout"]'
        assert row["stop_loss"] == pytest.approx(9.4)
        assert row["position_pct"] == pytest.approx(0.08)


# ---- market 隔离 -------------------------------------------------------------------


def test_kline_market_isolation_cn_us_same_code(db_path: Path) -> None:
    with Repository(db_path) as repo:
        repo.upsert_kline([_kline_row(code="0700", market="CN", close=1.0)])
        repo.upsert_kline([_kline_row(code="0700", market="US", close=999.0)])

        cn_df = repo.get_kline("0700", "CN")
        us_df = repo.get_kline("0700", "US")
        assert len(cn_df) == 1 and cn_df.iloc[0]["close"] == pytest.approx(1.0)
        assert len(us_df) == 1 and us_df.iloc[0]["close"] == pytest.approx(999.0)


def test_valuation_market_isolation(db_path: Path) -> None:
    with Repository(db_path) as repo:
        repo.upsert_valuation([_valuation_row(code="0700", market="CN", pe_ttm=10.0)])
        repo.upsert_valuation([_valuation_row(code="0700", market="US", pe_ttm=20.0)])

        assert repo.get_valuation("0700", "CN").iloc[0]["pe_ttm"] == pytest.approx(10.0)
        assert repo.get_valuation("0700", "US").iloc[0]["pe_ttm"] == pytest.approx(20.0)


def test_signals_market_isolation(db_path: Path) -> None:
    with Repository(db_path) as repo:
        repo.insert_signal(
            strategy_id="s1", date="2024-01-02", code="0700", market="CN", advice="buy"
        )
        repo.insert_signal(
            strategy_id="s1", date="2024-01-02", code="0700", market="US", advice="avoid"
        )

        cn_signals = repo.get_signals("CN", "2024-01-02")
        us_signals = repo.get_signals("US", "2024-01-02")
        assert len(cn_signals) == 1 and cn_signals.iloc[0]["advice"] == "buy"
        assert len(us_signals) == 1 and us_signals.iloc[0]["advice"] == "avoid"


# ---- 建表幂等 -----------------------------------------------------------------------


def test_repository_init_is_idempotent_against_existing_db(db_path: Path) -> None:
    repo1 = Repository(db_path)
    repo1.upsert_kline([_kline_row()])
    repo1.close()

    # 对已存在、已有数据的库重新执行 executescript 建表：不报错，数据保留。
    repo2 = Repository(db_path)
    try:
        df = repo2.get_kline("600000", "CN")
        assert len(df) == 1
    finally:
        repo2.close()


# ---- 生命周期 -----------------------------------------------------------------------


def test_close_is_idempotent(db_path: Path) -> None:
    repo = Repository(db_path)
    repo.close()
    repo.close()  # 不应报错


def test_write_after_close_raises(db_path: Path) -> None:
    repo = Repository(db_path)
    repo.close()
    with pytest.raises(RuntimeError, match="已关闭"):
        repo.upsert_kline([_kline_row()])


def test_context_manager_closes_writer_on_exit(db_path: Path) -> None:
    with Repository(db_path) as repo:
        repo.upsert_kline([_kline_row()])
    with pytest.raises(RuntimeError, match="已关闭"):
        repo.upsert_kline([_kline_row()])


# ---- 连接健壮性（代码审查发现的回归用例） -------------------------------------------


def test_db_path_with_uri_special_characters_reads_correctly(tmp_path: Path) -> None:
    """db 路径含 `?`/`#` 等 URI 保留字符时，只读连接必须仍能定位到同一份文件。

    修复前：`_connect_reader` 直接用 f-string 拼 `file:{path}?mode=ro`，未做百分号
    编码；路径里的 `?`/`#` 会被误判为查询串/片段起点，导致读连接打开了错误的
    （或不存在的）文件，写连接却因为没走 URI 形式而毫无问题——读写不一致。
    """
    db_path = tmp_path / "tally #1 ?weird.db"
    with Repository(db_path) as repo:
        repo.upsert_kline([_kline_row()])
        df = repo.get_kline("600000", "CN")
        assert len(df) == 1
        assert df.iloc[0]["close"] == pytest.approx(10.2)


class _FakeCursor:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class _FakeDegradedWalConnection:
    """模拟"发了 PRAGMA journal_mode=WAL，但实际生效值不是 wal"的连接。"""

    def __init__(self) -> None:
        self.closed = False
        self.row_factory = None

    def execute(self, sql: str, *args: object) -> _FakeCursor:
        if "journal_mode" in sql:
            return _FakeCursor(("delete",))  # 模拟 WAL 启用失败、静默回退到 delete 模式
        return _FakeCursor(None)

    def close(self) -> None:
        self.closed = True


def test_repository_rejects_silently_degraded_journal_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRAGMA journal_mode 失败时 SQLite 不报错，只静默回退——必须显式校验返回值
    并拒绝继续运行，而不是"发了 PRAGMA 就当作生效"。"""
    fake_conn = _FakeDegradedWalConnection()
    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: fake_conn)

    with pytest.raises(RuntimeError, match="无法启用 WAL"):
        Repository(tmp_path / "tally.db")
    assert fake_conn.closed
