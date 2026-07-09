"""M1 T1.1 唯一硬 AC：并发读写压测无 `sqlite3.OperationalError: database is locked`。

多个写线程通过公开方法并发提交写任务（内部经单写线程队列串行化），同时多个读线程
用各自独立只读连接反复查询；断言：
1. 全程不出现任何异常（尤其是 `database is locked`）；
2. 写入最终一致——共享 PK 集合下 upsert 不产生重复行、每次 `insert_signal`
   都成功落地且不丢失。

补测（代码审查发现的覆盖缺口）：`upsert_valuation` 此前未在高并发下与
kline/signals 一起验证过——三者共享同一条 WriteQueue，若只测 kline+signals，
valuation 那一路的并发正确性完全没有回归保护，故让 valuation 也参与本压测。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from tally.data.repository import Repository

# 压测规模：几百~上千次操作（spec 原话）。8 写 + 8 读线程、每写线程 60 次双写
# （kline upsert + insert_signal）= 960 次写操作，另加约 1600 次读操作。
_N_WRITER_THREADS = 8
_N_READER_THREADS = 8
_OPS_PER_WRITER = 60
_OPS_PER_READER = 100

# 20 个共享 PK：让所有写线程反复对同一批 (code, market, date) 做 upsert，
# 制造真实的高竞争场景（而不是每个线程各写各的、天然无冲突）。
_SHARED_DATES = [f"2024-02-{day:02d}" for day in range(1, 21)]
_SHARED_CODE = "SHARED"
_SHARED_MARKET = "CN"
_SIGNAL_DATE = "2024-02-01"


def _writer_job(
    repo: Repository,
    writer_idx: int,
    errors: list[BaseException],
    lock: threading.Lock,
) -> None:
    try:
        for i in range(_OPS_PER_WRITER):
            date = _SHARED_DATES[i % len(_SHARED_DATES)]
            repo.upsert_kline(
                [
                    {
                        "code": _SHARED_CODE,
                        "market": _SHARED_MARKET,
                        "date": date,
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": float(writer_idx * 100_000 + i),
                        "volume": 1.0,
                        "amount": 1.0,
                        "adj_factor": 1.0,
                    }
                ]
            )
            repo.upsert_valuation(
                [
                    {
                        "code": _SHARED_CODE,
                        "market": _SHARED_MARKET,
                        "date": date,
                        "pe_ttm": float(writer_idx * 100_000 + i),
                        "pb": 1.0,
                        "market_cap": 1.0,
                        "turnover_amt": 1.0,
                    }
                ]
            )
            repo.insert_signal(
                strategy_id="s1",
                date=_SIGNAL_DATE,
                code=f"W{writer_idx:02d}-{i}",
                market=_SHARED_MARKET,
                advice="buy",
            )
    except BaseException as exc:  # noqa: BLE001 — 压测需要捕获任何异常类型以断言"无 lock 错误"
        with lock:
            errors.append(exc)


def _reader_job(repo: Repository, errors: list[BaseException], lock: threading.Lock) -> None:
    try:
        for _ in range(_OPS_PER_READER):
            repo.get_kline(_SHARED_CODE, _SHARED_MARKET)
            repo.get_signals(_SHARED_MARKET, _SIGNAL_DATE)
            repo.get_valuation(_SHARED_CODE, _SHARED_MARKET)
    except BaseException as exc:  # noqa: BLE001
        with lock:
            errors.append(exc)


@pytest.mark.parametrize("run", range(3))  # 多跑几次以验证不 flaky（spec 门禁要求）
def test_concurrent_read_write_no_lock_errors_and_final_consistency(
    tmp_path: Path, run: int
) -> None:
    db_path = tmp_path / f"concurrency_{run}.db"
    repo = Repository(db_path)
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    threads = [
        threading.Thread(target=_writer_job, args=(repo, idx, errors, errors_lock))
        for idx in range(_N_WRITER_THREADS)
    ] + [
        threading.Thread(target=_reader_job, args=(repo, errors, errors_lock))
        for _ in range(_N_READER_THREADS)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive(), "线程未在超时内结束，疑似死锁"

    try:
        lock_errors = [
            e
            for e in errors
            if isinstance(e, sqlite3.OperationalError) and "locked" in str(e).lower()
        ]
        assert not lock_errors, f"出现 database is locked 错误：{lock_errors}"
        assert not errors, f"并发过程中出现其他异常：{errors}"

        # 最终一致性：20 个共享 PK 被反复 upsert，不应产生重复行。
        kline_df = repo.get_kline(_SHARED_CODE, _SHARED_MARKET)
        assert len(kline_df) == len(_SHARED_DATES)
        assert kline_df["date"].nunique() == len(_SHARED_DATES)

        valuation_df = repo.get_valuation(_SHARED_CODE, _SHARED_MARKET)
        assert len(valuation_df) == len(_SHARED_DATES)
        assert valuation_df["date"].nunique() == len(_SHARED_DATES)

        # 每次 insert_signal 用独立 code，互不冲突：数量应精确等于写入次数。
        signals_df = repo.get_signals(_SHARED_MARKET, _SIGNAL_DATE)
        assert len(signals_df) == _N_WRITER_THREADS * _OPS_PER_WRITER
        assert signals_df["id"].nunique() == _N_WRITER_THREADS * _OPS_PER_WRITER
    finally:
        repo.close()
