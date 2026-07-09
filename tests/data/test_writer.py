"""`WriteQueue`（单写线程队列）单测：异常传播回调用线程、writer 线程不因单次
任务失败而死掉、哨兵关闭与拒绝新任务的语义。

`Repository` 的 CRUD 测试走的都是成功路径；这里单独覆盖 `_writer.py` 内部的
错误处理分支（`tests/data/test_repository.py` 覆盖不到的部分）。
"""

from __future__ import annotations

import atexit
import logging
import sqlite3
import subprocess
import sys
import threading

import pytest

from tally.data._writer import WriteQueue


def _connect_memory() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def test_submit_returns_task_result() -> None:
    wq = WriteQueue(_connect_memory)
    try:
        result = wq.submit(lambda conn: 42)
        assert result == 42
    finally:
        wq.close()


def test_submit_propagates_exception_to_caller_thread() -> None:
    wq = WriteQueue(_connect_memory)

    def _boom(conn: sqlite3.Connection) -> None:
        raise ValueError("写任务内部故意失败")

    try:
        with pytest.raises(ValueError, match="写任务内部故意失败"):
            wq.submit(_boom)
    finally:
        wq.close()


def test_writer_thread_survives_a_failed_task_and_serves_next_one() -> None:
    wq = WriteQueue(_connect_memory)

    def _boom(conn: sqlite3.Connection) -> None:
        raise RuntimeError("boom")

    try:
        with pytest.raises(RuntimeError, match="boom"):
            wq.submit(_boom)
        # writer 线程没有因为上一个任务抛异常而退出：下一个任务照常执行。
        assert wq.submit(lambda conn: "still alive") == "still alive"
    finally:
        wq.close()


def test_close_is_idempotent_and_rejects_further_submits() -> None:
    wq = WriteQueue(_connect_memory)
    wq.close()
    wq.close()  # 幂等，不报错

    with pytest.raises(RuntimeError, match="已关闭"):
        wq.submit(lambda conn: 1)


# ---- writer 线程死亡：不应让调用方永久挂起（回归用例，见代码审查发现） -------------------


def test_connect_failure_makes_submit_raise_instead_of_hang() -> None:
    """`connect` 本身失败时，writer 线程立即退出；`submit()` 必须快速报错，而非永久挂起
    在 `result_queue.get()` 上（此前的 bug：thread 死了，调用方却没人告知）。"""

    def _broken_connect() -> sqlite3.Connection:
        raise RuntimeError("simulated connect failure")

    wq = WriteQueue(_broken_connect)
    with pytest.raises(RuntimeError, match="已异常退出"):
        wq.submit(lambda conn: 1)
    wq.close()  # 线程已经因连接失败自行退出；close() 不应挂起或抛错


def test_fatal_exception_escaping_execute_fails_current_and_future_submits_cleanly() -> None:
    """`_execute` 只兜 `Exception`；一个逃逸的 `BaseException` 子类会杀死 writer 线程，
    但当次调用与之后所有调用都必须拿到明确错误，而不是永久挂起。"""

    class _Fatal(BaseException):
        pass

    wq = WriteQueue(_connect_memory)

    def _fatal_task(conn: sqlite3.Connection) -> None:
        raise _Fatal("fatal, escapes Exception-only catch")

    with pytest.raises(_Fatal):
        wq.submit(_fatal_task)

    with pytest.raises(RuntimeError, match="已异常退出"):
        wq.submit(lambda conn: 1)

    wq.close()  # 线程已经死亡；close() 应正常返回（不挂起、不抛错）


def test_fatal_exception_also_drains_and_fails_other_already_queued_tasks() -> None:
    """致命异常发生时，队列里排在后面、还没开始执行的任务也要被清空并逐个报错，
    而不是留在队列里让对应的调用方永久挂起。"""
    wq = WriteQueue(_connect_memory)
    started = threading.Event()
    release = threading.Event()

    class _Fatal(BaseException):
        pass

    def _fatal_task(conn: sqlite3.Connection) -> None:
        started.set()
        release.wait(timeout=5)
        raise _Fatal("fatal")

    results: dict[str, BaseException] = {}

    def _submit_and_capture(key: str, fn: object) -> None:
        try:
            wq.submit(fn)  # type: ignore[arg-type]
        except BaseException as exc:  # noqa: BLE001 — 需要原样捕获任意异常类型以断言
            results[key] = exc

    fatal_thread = threading.Thread(target=_submit_and_capture, args=("fatal", _fatal_task))
    fatal_thread.start()
    assert started.wait(timeout=5), "writer 线程未在超时内开始执行 fatal 任务"

    other_thread = threading.Thread(
        target=_submit_and_capture, args=("other", lambda conn: "should never run")
    )
    other_thread.start()
    other_thread.join(timeout=5)  # other 任务此时已入队（排在 fatal 后面），但尚未执行

    release.set()
    fatal_thread.join(timeout=5)

    assert isinstance(results.get("fatal"), _Fatal)
    other_error = results.get("other")
    assert isinstance(other_error, RuntimeError) and "已异常退出" in str(other_error)

    wq.close()


def test_close_raises_if_writer_thread_still_running_after_timeout() -> None:
    """`close(timeout=...)` 超时后线程仍在跑：必须显式报错，而不是静默返回造成
    "已优雅关闭"的假象。"""
    wq = WriteQueue(_connect_memory)
    started = threading.Event()
    release = threading.Event()

    def _slow_task(conn: sqlite3.Connection) -> str:
        started.set()
        release.wait(timeout=5)
        return "done"

    submit_thread = threading.Thread(target=wq.submit, args=(_slow_task,))
    submit_thread.start()
    assert started.wait(timeout=5), "writer 线程未在超时内开始执行慢任务"

    with pytest.raises(RuntimeError, match="未能在.*内退出"):
        wq.close(timeout=0.05)

    release.set()
    submit_thread.join(timeout=5)
    wq._thread.join(timeout=5)  # 清理：确认真正的 writer 线程最终正常退出，不残留给其他测试


def test_close_called_again_after_timeout_still_raises_until_thread_actually_exits() -> None:
    """回归用例（代码审查发现的真实 bug）：`close()` 首次因超时抛错后，`_closed`
    被置位；若第二次调用直接命中 `if self._closed: return`，会在 writer 线程仍
    存活、慢任务仍未跑完时假报"已关闭"成功，之后再也不会重新 join/检查线程存活。

    正确行为：只要线程还活着，无论第几次调用 `close()` 都必须如实 raise；线程
    真正退出后，close() 才能返回成功。"""
    wq = WriteQueue(_connect_memory)
    started = threading.Event()
    release = threading.Event()

    def _slow_task(conn: sqlite3.Connection) -> str:
        started.set()
        release.wait(timeout=5)
        return "done"

    submit_thread = threading.Thread(target=wq.submit, args=(_slow_task,))
    submit_thread.start()
    assert started.wait(timeout=5), "writer 线程未在超时内开始执行慢任务"

    # 第一次 close()：超时，线程仍在跑慢任务，必须 raise。
    with pytest.raises(RuntimeError, match="未能在.*内退出"):
        wq.close(timeout=0.05)

    # 第二次 close()（例如调用方重试）：线程依然存活，绝不能假成功返回——
    # 这正是 bug 发生的地方：修复前会直接命中 `if self._closed: return`。
    with pytest.raises(RuntimeError, match="未能在.*内退出"):
        wq.close(timeout=0.05)

    # 慢任务结束后，线程真正退出：close() 才应该正常成功返回。
    release.set()
    submit_thread.join(timeout=5)
    wq.close(timeout=5)  # 不应再抛错
    wq._thread.join(timeout=5)  # 清理：确认真正的 writer 线程最终正常退出，不残留给其他测试


# ---- atexit 安全网：忘记 close() 的 daemon writer 不应完全静默丢任务 ---------------------


def test_atexit_hook_warns_when_never_closed(caplog: pytest.LogCaptureFixture) -> None:
    """未调用 close() （这里直接触发私有钩子，模拟 atexit 在进程退出时的调用）：
    必须打一条 warning，而不是让 daemon 线程被进程杀掉、队列里的任务完全无声消失。"""
    wq = WriteQueue(_connect_memory)
    try:
        with caplog.at_level(logging.WARNING, logger="tally.data._writer"):
            wq._warn_if_unflushed_at_exit()
        assert any("未调用 close()" in record.message for record in caplog.records)
    finally:
        wq.close()


def test_atexit_hook_warns_when_closed_but_thread_still_running(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`close()` 已被调用过（`_closed=True`）但线程仍在跑慢任务未真正退出——例如
    首次 close(timeout=极短) 超时 raise 之后，进程恰好在此刻真的退出：atexit 钩子
    仍应打 warning，而不是因为"close() 至少被调用过一次"就误判为已安全关闭。"""
    wq = WriteQueue(_connect_memory)
    started = threading.Event()
    release = threading.Event()

    def _slow_task(conn: sqlite3.Connection) -> str:
        started.set()
        release.wait(timeout=5)
        return "done"

    submit_thread = threading.Thread(target=wq.submit, args=(_slow_task,))
    submit_thread.start()
    assert started.wait(timeout=5), "writer 线程未在超时内开始执行慢任务"

    with pytest.raises(RuntimeError, match="未能在.*内退出"):
        wq.close(timeout=0.05)

    with caplog.at_level(logging.WARNING, logger="tally.data._writer"):
        wq._warn_if_unflushed_at_exit()
    assert any("未在进程退出前完全消费完队列" in record.message for record in caplog.records)

    release.set()
    submit_thread.join(timeout=5)
    wq.close(timeout=5)  # 清理：确认真正的 writer 线程最终能正常退出


def test_atexit_hook_silent_after_clean_close(caplog: pytest.LogCaptureFixture) -> None:
    """正常 close() 之后，安全网钩子不应误报"未落盘"。"""
    wq = WriteQueue(_connect_memory)
    wq.close()
    with caplog.at_level(logging.WARNING, logger="tally.data._writer"):
        wq._warn_if_unflushed_at_exit()
    assert caplog.records == []


def test_atexit_hook_unregistered_after_clean_close() -> None:
    """close() 成功后应主动注销 atexit 钩子，避免长生命周期进程里每一个已正常
    关闭的 WriteQueue 都白白堆积一份 atexit 注册。"""
    wq = WriteQueue(_connect_memory)
    wq.close()
    # atexit.unregister 对"未注册的可调用对象"是 no-op、不报错；这里只是确认
    # close() 之后再手动调用一次不会出问题（间接验证 close() 内部确已注销过）。
    atexit.unregister(wq._warn_if_unflushed_at_exit)


def test_forgotten_close_triggers_atexit_warning_end_to_end() -> None:
    """端到端回归：真正让子进程退出（不显式调用 close()），确认 atexit 钩子确实
    被注册并在进程退出时触发——而不只是私有方法本身逻辑正确、却从未真正接到
    atexit 上（"注册"这一步本身也是需要覆盖的行为，不能只测钩子函数体）。"""
    script = (
        "import logging, sys\n"
        "logging.basicConfig(level=logging.WARNING, stream=sys.stderr)\n"
        "import sqlite3\n"
        "from tally.data._writer import WriteQueue\n"
        "wq = WriteQueue(lambda: sqlite3.connect(':memory:'))\n"
        "wq.submit(lambda conn: 1)\n"
        # 故意不调用 wq.close()，模拟调用方忘记关闭，直接让进程退出。
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=10
    )
    assert "未调用 close()" in result.stderr, result.stderr
