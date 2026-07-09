"""单写线程队列（IMPLEMENTATION_SPEC.md §3.3"多线程拉取单线程批量写"）。

所有写操作封成任务投递到 `queue.Queue`，由唯一的 writer 线程串行消费、每个任务
在同一事务内执行；`sqlite3` 的 `busy_timeout` PRAGMA 作兜底，但正常路径下
写者之间根本不会竞争同一把锁。读操作不经此队列，见 `repository.py` 的只读连接。

纯工程基建，不含业务参数。
"""

from __future__ import annotations

import atexit
import logging
import queue
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, cast

_logger = logging.getLogger(__name__)

_STOP: object = object()

_T = TypeVar("_T")


@dataclass
class _WriteTask(Generic[_T]):
    """一个待执行的写任务：`fn` 接收 writer 线程持有的连接，返回值原样传回调用方。"""

    fn: Callable[[sqlite3.Connection], _T]
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
        self._tasks: queue.Queue[_WriteTask[Any] | object] = queue.Queue()
        self._submit_lock = threading.Lock()
        # `_closed`：是否已发起过关闭（即 close() 被调用过至少一次）——一旦为真，
        # 拒绝所有新的 submit()，且无论本次 close() 最终是否等到线程退出都不会回退。
        # `_stop_submitted`：`_STOP` 哨兵是否已投递过——只投递一次，避免重复调用
        # close() 时把哨兵重复塞进队列。
        # 这两者与"线程是否真的已退出"（`_thread.is_alive()`）是三件独立的事：
        # 之前的 bug 就是把"已发起关闭"和"已确认关闭成功"合并成同一个 `_closed`
        # 标志，导致首次 close() 超时报错后，第二次 close() 直接命中
        # `if self._closed: return` 假成功返回，不再 join、不再检查线程存活。
        self._closed = False
        self._stop_submitted = False
        self._broken_error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="tally-repo-writer", daemon=True)
        self._thread.start()
        # 安全网：调用方忘记 close()（或 close() 未能等到线程真正退出）就让进程
        # 退出时，daemon 线程会被直接杀掉，队列里尚未执行的写任务随之静默丢失。
        # atexit 在此兜底打一条 warning，而不是让数据丢失完全无声无息。
        atexit.register(self._warn_if_unflushed_at_exit)

    def submit(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        """投递写任务并阻塞等待其在 writer 线程执行完毕；异常在调用线程原样重新抛出。"""
        task: _WriteTask[_T] = _WriteTask(fn=fn)
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
        return cast(_T, payload)

    def close(self, timeout: float | None = 5.0) -> None:
        """投递哨兵任务（仅首次调用投递），等待 writer 线程退出。

        每次调用都会重新 `join` 并检查线程是否仍存活——不因为"之前已经调用过
        close()"就跳过这一步。若 `timeout` 内线程未退出（仍在处理任务，或已因
        `_broken_error` 死锁式卡住——理论上不会，但作为显式契约），抛出
        `RuntimeError` 而不是静默返回，避免调用方误以为已完成优雅关闭；这对
        "第二次/第 N 次调用 close()"同样成立——只有线程确实退出后再调用才会
        正常返回，绝不会因为哨兵已投递过就假报成功。
        """
        with self._submit_lock:
            self._closed = True
            if not self._stop_submitted:
                self._stop_submitted = True
                self._tasks.put(_STOP)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise RuntimeError(f"writer 线程未能在 {timeout}s 内退出，关闭未完成")
        # 线程确认已退出：注销 atexit 钩子，避免长生命周期进程里每一个已正常
        # 关闭的 WriteQueue 都白白占着一份 atexit 注册（且防止误报"未落盘"）。
        atexit.unregister(self._warn_if_unflushed_at_exit)

    def _warn_if_unflushed_at_exit(self) -> None:
        """atexit 安全网：进程退出时若本队列未经正常 close()、或线程未真正
        退出/队列仍有残留任务，说明可能存在尚未落盘就被丢弃的写任务。"""
        if not self._closed:
            _logger.warning(
                "WriteQueue 在进程退出前未调用 close()：可能存在尚未落盘的写任务，"
                "已随进程退出而丢失。请通过 close() 或上下文管理器正常关闭 Repository。"
            )
            return
        if self._thread.is_alive() or not self._tasks.empty():
            _logger.warning(
                "WriteQueue 已调用 close() 但 writer 线程未在进程退出前完全消费完队列："
                "可能存在尚未落盘的写任务，已随进程退出而丢失。"
            )

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
            # 显式设置 __cause__（等价于 `raise ... from exc`，只是这里不是直接
            # raise，而是把异常对象放进队列，由调用线程的 `submit()` 后续
            # `raise payload` 时再真正抛出）：保持异常链一致，排障时能看到
            # writer 线程真正的死因，而非一个孤立、无上下文的 RuntimeError。
            task_error = RuntimeError("writer 线程已异常退出，无法执行该任务")
            task_error.__cause__ = exc
            pending.result_queue.put((False, task_error))

    @staticmethod
    def _execute(conn: sqlite3.Connection, task: _WriteTask[Any]) -> None:
        try:
            result = task.fn(conn)
        except Exception as exc:  # noqa: BLE001 — 异常需带回调用线程，而非杀死 writer 线程
            conn.rollback()
            task.result_queue.put((False, exc))
        else:
            task.result_queue.put((True, result))
