"""单写线程队列（IMPLEMENTATION_SPEC.md §3.3"多线程拉取单线程批量写"）。

所有写操作封成任务投递到 `queue.Queue`，由唯一的 writer 线程串行消费、每个任务
在同一事务内执行；`sqlite3` 的 `busy_timeout` PRAGMA 作兜底，但正常路径下
写者之间根本不会竞争同一把锁。读操作不经此队列，见 `repository.py` 的只读连接。

纯工程基建，不含业务参数。
"""

from __future__ import annotations

import queue
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_STOP: object = object()


@dataclass
class _WriteTask:
    """一个待执行的写任务：`fn` 接收 writer 线程持有的连接，返回值原样传回调用方。"""

    fn: Callable[[sqlite3.Connection], Any]
    result_queue: queue.Queue[tuple[bool, Any]] = field(
        default_factory=lambda: queue.Queue(maxsize=1)
    )


class WriteQueue:
    """单写线程模型：一个 writer 线程 + `queue.Queue`，写任务串行执行。

    writer 线程本身异常死亡（连接失败，或任务里跑出未被 `_execute` 兜住的
    `BaseException`，如 `SystemExit`/`KeyboardInterrupt`）是一等要处理的故障模式：
    不能让调用方永久卡在 `result_queue.get()` 上。一旦发生，`_broken_error` 被
    置位，队列中所有在等/后续再提交的任务都会立刻拿到明确的 `RuntimeError`，
    而不是静默挂起。
    """

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect
        self._tasks: queue.Queue[_WriteTask | object] = queue.Queue()
        self._submit_lock = threading.Lock()
        self._closed = False
        self._broken_error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="tally-repo-writer", daemon=True)
        self._thread.start()

    def submit(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """投递写任务并阻塞等待其在 writer 线程执行完毕；异常在调用线程原样重新抛出。"""
        task = _WriteTask(fn=fn)
        with self._submit_lock:
            if self._closed:
                raise RuntimeError("WriteQueue 已关闭，不能再提交写任务")
            if self._broken_error is not None:
                raise RuntimeError(
                    "writer 线程已异常退出，Repository 不可用"
                ) from self._broken_error
            self._tasks.put(task)
        ok, payload = task.result_queue.get()
        if not ok:
            raise payload
        return payload

    def close(self, timeout: float | None = 5.0) -> None:
        """投递哨兵任务，等待 writer 线程消费完队列中所有已提交任务后退出。幂等。

        若 `timeout` 内线程未退出（仍在处理任务，或已因 `_broken_error` 死锁式卡住
        ——理论上不会，但作为显式契约），抛出 `RuntimeError` 而不是静默返回，
        避免调用方误以为已完成优雅关闭。
        """
        with self._submit_lock:
            if self._closed:
                return
            self._closed = True
            self._tasks.put(_STOP)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise RuntimeError(f"writer 线程未能在 {timeout}s 内退出，关闭未完成")

    def _run(self) -> None:
        try:
            conn = self._connect()
        except (
            BaseException
        ) as exc:  # noqa: BLE001 — 连接失败也要唤醒所有等待者，而非让它们永久挂起
            self._fail_all_pending(exc)
            return
        try:
            while True:
                task = self._tasks.get()
                if task is _STOP:
                    break
                assert isinstance(task, _WriteTask)
                try:
                    self._execute(conn, task)
                except BaseException as exc:  # noqa: BLE001 — 防御 `_execute` 之外逃逸的致命异常
                    task.result_queue.put((False, exc))
                    self._fail_all_pending(exc)
                    return
        finally:
            conn.close()

    def _fail_all_pending(self, exc: BaseException) -> None:
        """writer 线程即将死亡：标记 broken，并让队列里所有已提交但还没执行的任务
        立刻失败，避免调用方永久挂起在 `result_queue.get()` 上。"""
        with self._submit_lock:
            self._broken_error = exc
        while True:
            try:
                pending = self._tasks.get_nowait()
            except queue.Empty:
                break
            if pending is _STOP:
                continue
            assert isinstance(pending, _WriteTask)
            pending.result_queue.put((False, RuntimeError("writer 线程已异常退出，无法执行该任务")))

    @staticmethod
    def _execute(conn: sqlite3.Connection, task: _WriteTask) -> None:
        try:
            result = task.fn(conn)
        except Exception as exc:  # noqa: BLE001 — 异常需带回调用线程，而非杀死 writer 线程
            conn.rollback()
            task.result_queue.put((False, exc))
        else:
            task.result_queue.put((True, result))
