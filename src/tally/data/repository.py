"""唯一 SQL 入口（IMPLEMENTATION_SPEC.md 铁律 3："所有持久化经 Repository"）。

M1 T1.1 范围：kline / valuation / signals 三表的最小 Repository。

- 写：全部经 `WriteQueue`（单写线程，见 `_writer.py`）串行执行，一次公开方法调用
  = 一个事务（`executemany` + 单次 `commit`）。
- 读：每次调用开一条独立的只读连接（`sqlite3.connect("file:...?mode=ro", uri=True)`），
  互不阻塞、也不阻塞写者。
- upsert 用 SQLite 原生 `INSERT ... ON CONFLICT(...) DO UPDATE`（禁用
  `INSERT OR REPLACE`——为后续可能迁移到 PostgreSQL 保留方言纪律）。
- PRAGMA 取值（WAL / busy_timeout=30000 / synchronous=NORMAL / foreign_keys=ON）是
  IMPLEMENTATION_SPEC.md §3.2 写死的工程常量，不是可调业务参数，故不进
  `config/*.yaml`；db 路径本身走构造参数，由调用方（如 `tally.common.config`）决定。
"""

from __future__ import annotations

import sqlite3
import urllib.parse
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any

import pandas as pd

from tally.data._rows import normalize_kline_rows, normalize_valuation_rows
from tally.data._writer import WriteQueue

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_BUSY_TIMEOUT_MS = 30_000
_CONNECT_TIMEOUT_S = _BUSY_TIMEOUT_MS / 1000


class Repository:
    """kline / valuation / signals 三表的 Repository（单写线程 + WAL）。"""

    def __init__(self, db_path: str | Path, *, schema_path: Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_path = schema_path or _SCHEMA_PATH
        self._init_schema()
        self._writer = WriteQueue(self._connect_writer)

    # ---- 连接管理 ------------------------------------------------------------

    def _connect_writer(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=_CONNECT_TIMEOUT_S)
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        # journal_mode=WAL 是整套单写线程+多只读连接模型的地基；PRAGMA 失败时 SQLite
        # 不报错，只是静默回退到实际生效的模式（例如某些网络文件系统不支持 WAL），
        # 所以必须读回结果并显式校验，而不是"发了 PRAGMA 就当作生效"。
        (journal_mode,) = conn.execute("PRAGMA journal_mode = WAL").fetchone()
        if str(journal_mode).lower() != "wal":
            conn.close()
            raise RuntimeError(
                f"无法启用 WAL 模式（PRAGMA journal_mode 实际生效值为 {journal_mode!r}）；"
                "单写线程 + 多只读连接模型依赖 WAL，拒绝以其他模式继续运行"
            )
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _connect_reader(self) -> sqlite3.Connection:
        # sqlite3 的 URI 文件名要求路径里的 ?/#/% 等字符被百分号编码，否则会被误判为
        # 查询串/片段的起点，导致同一个 db_path 在读连接上打开了错误的（或不存在的）
        # 文件，而写连接（走非 URI 形式）却毫无问题——一种极易误诊的读写不一致。
        # 额外 resolve() 到绝对路径：URI 相对路径按进程 cwd 解释，与写连接的直接路径
        # 语义可能不一致，resolve() 后两者保证指向同一份文件。
        quoted_path = urllib.parse.quote(str(self._db_path.resolve()))
        uri = f"file:{quoted_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=_CONNECT_TIMEOUT_S)
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        """建表（幂等：schema.sql 的 CREATE TABLE/INDEX 均带 IF NOT EXISTS）。"""
        conn = self._connect_writer()
        try:
            conn.executescript(self._schema_path.read_text(encoding="utf-8"))
            conn.commit()
        finally:
            conn.close()

    # ---- 写：kline ------------------------------------------------------------

    _UPSERT_KLINE_SQL = """
        INSERT INTO kline
            (code, market, date, open, high, low, close, volume, amount, adj_factor, source)
        VALUES
            (:code, :market, :date, :open, :high, :low, :close, :volume, :amount,
             :adj_factor, :source)
        ON CONFLICT(code, market, date) DO UPDATE SET
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            amount = excluded.amount,
            adj_factor = excluded.adj_factor,
            source = excluded.source
    """

    def upsert_kline(self, rows: pd.DataFrame | Iterable[Mapping[str, Any]]) -> int:
        """批量 upsert kline 行（PK 冲突整行覆盖）；返回本次提交的行数。"""
        records = normalize_kline_rows(rows)
        if not records:
            return 0

        def _task(conn: sqlite3.Connection) -> int:
            conn.executemany(self._UPSERT_KLINE_SQL, records)
            conn.commit()
            return len(records)

        return self._writer.submit(_task)

    # ---- 写：valuation --------------------------------------------------------

    _UPSERT_VALUATION_SQL = """
        INSERT INTO valuation (code, market, date, pe_ttm, pb, market_cap, turnover_amt)
        VALUES (:code, :market, :date, :pe_ttm, :pb, :market_cap, :turnover_amt)
        ON CONFLICT(code, market, date) DO UPDATE SET
            pe_ttm = excluded.pe_ttm,
            pb = excluded.pb,
            market_cap = excluded.market_cap,
            turnover_amt = excluded.turnover_amt
    """

    def upsert_valuation(self, rows: pd.DataFrame | Iterable[Mapping[str, Any]]) -> int:
        """批量 upsert valuation 行（PK 冲突整行覆盖）；返回本次提交的行数。"""
        records = normalize_valuation_rows(rows)
        if not records:
            return 0

        def _task(conn: sqlite3.Connection) -> int:
            conn.executemany(self._UPSERT_VALUATION_SQL, records)
            conn.commit()
            return len(records)

        return self._writer.submit(_task)

    # ---- 写：signals ------------------------------------------------------------

    _INSERT_SIGNAL_SQL = """
        INSERT INTO signals
            (strategy_id, date, code, market, advice, score,
             reasons_json, price_at_signal, stop_loss, position_pct)
        VALUES
            (:strategy_id, :date, :code, :market, :advice, :score,
             :reasons_json, :price_at_signal, :stop_loss, :position_pct)
    """

    def insert_signal(
        self,
        *,
        strategy_id: str,
        date: str,
        code: str,
        market: str,
        advice: str,
        score: float | None = None,
        reasons_json: str | None = None,
        price_at_signal: float | None = None,
        stop_loss: float | None = None,
        position_pct: float | None = None,
    ) -> int:
        """插入一条 signal（自增 id），返回该行的 id。"""
        params: dict[str, Any] = {
            "strategy_id": strategy_id,
            "date": date,
            "code": code,
            "market": market,
            "advice": advice,
            "score": score,
            "reasons_json": reasons_json,
            "price_at_signal": price_at_signal,
            "stop_loss": stop_loss,
            "position_pct": position_pct,
        }

        def _task(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(self._INSERT_SIGNAL_SQL, params)
            conn.commit()
            signal_id = cursor.lastrowid
            # 显式 raise 而非 assert：assert 在 `python -O` 下会被整段剥除，
            # 这里的"插入成功后必有自增 id"是需要在任何运行模式下都成立的契约。
            if signal_id is None:
                raise RuntimeError("insert_signal 写入后 cursor.lastrowid 为 None，违反契约")
            return signal_id

        return self._writer.submit(_task)

    # ---- 读 --------------------------------------------------------------------

    def _read_df(self, sql: str, params: Sequence[Any]) -> pd.DataFrame:
        # 手写 cursor 取数而非 pd.read_sql_query：后者的 pandas-stubs 签名对
        # params 类型要求过窄（与本仓库实际传参形态不匹配），手写路径既绕开
        # 这一 stub 摩擦，也让"SQL 只出现在 Repository 内部"这条铁律更直白。
        conn = self._connect_reader()
        try:
            cursor = conn.execute(sql, params)
            column_names = [col[0] for col in cursor.description]
            rows = [dict(row) for row in cursor.fetchall()]
            return pd.DataFrame(rows, columns=column_names)
        finally:
            conn.close()

    @staticmethod
    def _date_range_clause(start: str | None, end: str | None) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if start is not None:
            clauses.append("date >= ?")
            params.append(start)
        if end is not None:
            clauses.append("date <= ?")
            params.append(end)
        return clauses, params

    def get_kline(
        self,
        code: str,
        market: str,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        """按 code+market（可选 [start, end] 闭区间）取 kline，按 date 升序。"""
        clauses = ["code = ?", "market = ?"]
        params: list[Any] = [code, market]
        extra_clauses, extra_params = self._date_range_clause(start, end)
        clauses += extra_clauses
        params += extra_params
        sql = (
            "SELECT code, market, date, open, high, low, close, volume, amount, "
            "adj_factor, source FROM kline "
            f"WHERE {' AND '.join(clauses)} ORDER BY date"
        )
        return self._read_df(sql, params)

    def get_valuation(
        self,
        code: str,
        market: str,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        """按 code+market（可选 [start, end] 闭区间）取 valuation，按 date 升序。"""
        clauses = ["code = ?", "market = ?"]
        params: list[Any] = [code, market]
        extra_clauses, extra_params = self._date_range_clause(start, end)
        clauses += extra_clauses
        params += extra_params
        sql = (
            "SELECT code, market, date, pe_ttm, pb, market_cap, turnover_amt FROM valuation "
            f"WHERE {' AND '.join(clauses)} ORDER BY date"
        )
        return self._read_df(sql, params)

    def get_signals(self, market: str, date: str) -> pd.DataFrame:
        """按 market+date 精确匹配取 signals，按 id 升序。"""
        sql = (
            "SELECT id, strategy_id, date, code, market, advice, score, reasons_json, "
            "price_at_signal, stop_loss, position_pct FROM signals "
            "WHERE market = ? AND date = ? ORDER BY id"
        )
        return self._read_df(sql, [market, date])

    # ---- 生命周期 ----------------------------------------------------------------

    def close(self, timeout: float | None = 5.0) -> None:
        """优雅停 writer 线程（投递哨兵任务并等待其消费完队列）。幂等。"""
        self._writer.close(timeout=timeout)

    def __enter__(self) -> Repository:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
