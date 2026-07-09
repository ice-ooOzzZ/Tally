"""M1 T1.2 单测：`TokenBucket` 速率型令牌桶 + `RateLimiterRegistry`。

时钟/睡眠均注入假实现：验证限流行为时不真正 `time.sleep`（否则拖慢测试），
但仍能断言"确实触发了等待、且等待时长符合速率模型"。
"""

from __future__ import annotations

import threading

import pytest

from tally.data.rate_limit import RateLimiterRegistry, TokenBucket


class _FakeClock:
    """手动推进的假时钟：`sleep_and_advance` 模拟"等待期间时间流逝"而不真的等待。"""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self._lock = threading.Lock()
        self.sleep_calls: list[float] = []

    def now(self) -> float:
        with self._lock:
            return self._now

    def sleep_and_advance(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        with self._lock:
            self._now += seconds


# ---- 基本行为 -----------------------------------------------------------------


def test_acquire_within_capacity_does_not_wait() -> None:
    clock = _FakeClock()
    bucket = TokenBucket(60.0, capacity=5.0, clock=clock.now, sleep=clock.sleep_and_advance)

    waited = bucket.acquire(3.0)

    assert waited == 0.0
    assert clock.sleep_calls == []
    assert bucket.available_tokens() == pytest.approx(2.0)


def test_acquire_blocks_until_refill_using_fake_clock() -> None:
    """容量恰好 1、速率 60/分钟（=1/秒）：连续拿两次各 1 个令牌，第二次必须等待约 1 秒。"""
    clock = _FakeClock()
    bucket = TokenBucket(60.0, capacity=1.0, clock=clock.now, sleep=clock.sleep_and_advance)

    first_wait = bucket.acquire(1.0)
    assert first_wait == 0.0  # 桶初始是满的

    second_wait = bucket.acquire(1.0)
    assert second_wait == pytest.approx(1.0, abs=1e-6)
    assert clock.sleep_calls == [pytest.approx(1.0, abs=1e-6)]


def test_acquire_multiple_tokens_at_once() -> None:
    clock = _FakeClock()
    bucket = TokenBucket(120.0, capacity=10.0, clock=clock.now, sleep=clock.sleep_and_advance)

    bucket.acquire(10.0)  # 耗尽整桶
    waited = bucket.acquire(4.0)  # 120/分钟 = 2/秒；4 个令牌需等 2 秒

    assert waited == pytest.approx(2.0, abs=1e-6)


def test_rate_per_min_must_be_positive() -> None:
    with pytest.raises(ValueError, match="rate_per_min"):
        TokenBucket(0.0)
    with pytest.raises(ValueError, match="rate_per_min"):
        TokenBucket(-10.0)


def test_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError, match="capacity"):
        TokenBucket(60.0, capacity=0.0)


def test_acquire_non_positive_tokens_raises() -> None:
    bucket = TokenBucket(60.0)
    with pytest.raises(ValueError, match="tokens"):
        bucket.acquire(0.0)
    with pytest.raises(ValueError, match="tokens"):
        bucket.acquire(-1.0)


def test_acquire_more_than_capacity_raises() -> None:
    bucket = TokenBucket(60.0, capacity=5.0)
    with pytest.raises(ValueError, match="超过桶容量"):
        bucket.acquire(6.0)


# ---- 限流确实生效（不超配额） ---------------------------------------------------


def test_rate_limiting_actually_caps_throughput_per_window() -> None:
    """速率 400/分钟（对齐 config `rate_limits.tushare_per_min`）、容量=速率：
    在"1 分钟窗口内"最多消费 400 个令牌，第 401 个必须等待——用假时钟验证不超配额，
    且断言等待时长与速率模型吻合（deficit / rate_per_sec）。
    """
    clock = _FakeClock()
    bucket = TokenBucket(400.0, clock=clock.now, sleep=clock.sleep_and_advance)

    for _ in range(400):
        assert bucket.acquire(1.0) == 0.0  # 初始满桶，400 次都不应等待

    waited = bucket.acquire(1.0)
    # rate_per_sec = 400/60；第 401 个令牌缺 1 个，等待时长 = 1 / (400/60) = 0.15s
    assert waited == pytest.approx(1.0 / (400.0 / 60.0), abs=1e-6)


# ---- 线程安全 -------------------------------------------------------------------


def test_thread_safety_concurrent_acquire_never_overdraws_bucket() -> None:
    """时钟恒定（不流逝）时，20 个线程各 `acquire(1)` 50 次、总计恰好等于容量：
    若并发状态被破坏（丢失更新/重复扣减），`available_tokens()` 会不等于 0，
    或者会有线程意外触发等待（因为看到了错误的剩余令牌数导致误判不足）。
    """
    clock = _FakeClock()
    total_requests = 20 * 50
    bucket = TokenBucket(
        60.0, capacity=float(total_requests), clock=clock.now, sleep=clock.sleep_and_advance
    )

    def _worker() -> None:
        for _ in range(50):
            bucket.acquire(1.0)

    threads = [threading.Thread(target=_worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert clock.sleep_calls == []  # 恰好用满容量，任何线程都不应触发等待
    assert bucket.available_tokens() == pytest.approx(0.0)


# ---- RateLimiterRegistry --------------------------------------------------------


def test_registry_get_unregistered_source_raises_keyerror_with_clear_message() -> None:
    registry = RateLimiterRegistry()
    with pytest.raises(KeyError, match="tushare"):
        registry.get("tushare")


def test_registry_register_and_get_roundtrip() -> None:
    registry = RateLimiterRegistry()
    bucket = TokenBucket(400.0)
    registry.register("tushare", bucket)

    assert registry.get("tushare") is bucket
    assert registry.sources() == ("tushare",)


def test_registry_get_or_create_returns_same_instance_on_repeated_calls() -> None:
    registry = RateLimiterRegistry()

    first = registry.get_or_create("tushare", 400.0)
    second = registry.get_or_create("tushare", 999.0)  # 已存在则忽略新参数，返回同一实例

    assert first is second
    assert registry.get("tushare") is first


def test_registry_supports_multiple_independent_sources() -> None:
    """便于后续加 yfinance/sec：不同 source 互不影响。"""
    registry = RateLimiterRegistry()
    tushare_bucket = registry.get_or_create("tushare", 400.0, capacity=1.0)
    yfinance_bucket = registry.get_or_create("yfinance", 120.0, capacity=1.0)

    tushare_bucket.acquire(1.0)  # 耗尽 tushare 的桶

    assert yfinance_bucket.available_tokens() == pytest.approx(1.0)  # 互不影响
