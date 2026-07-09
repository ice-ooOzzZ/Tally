"""全局令牌桶（IMPLEMENTATION_SPEC.md §3.3："全局令牌桶……多线程拉取单线程批量写"）。

- `TokenBucket`：速率型令牌桶，线程安全（单个 `threading.Lock` 保护状态），
  时钟与睡眠均可注入（默认 `time.monotonic` / `time.sleep`），使单测可以用
  假时钟驱动、不真正 sleep 也不牺牲对"限流确实生效"的验证。
- `RateLimiterRegistry`：按数据源名称（如 `"tushare"`）持有独立的令牌桶，
  供 `data/sync.py`（T1.3 起）与各 `data/sources/*.py` 适配器共享同一份限流
  状态；后续新增 `akshare`/`yfinance`/`sec_edgar` 数据源时只需 `register`
  或 `get_or_create` 各自的桶，无需改动本模块。

速率单位统一为"每分钟"（对齐 `config/system.yaml` 的 `rate_limits.tushare_per_min`
等字段名），内部换算为每秒速率驱动补充。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

Clock = Callable[[], float]
Sleep = Callable[[float], None]


class TokenBucket:
    """速率型令牌桶：`acquire(n)` 在令牌不足时阻塞等待补充到位再返回。

    - 容量默认等于每分钟速率（即最多允许攒够"一分钟额度"的突发）；可通过
      `capacity` 覆盖为更小的桶以收紧突发。
    - 补充做法：每次 `acquire` 时按"距上次补充的时间差 × 每秒速率"补充令牌，
      而不是起后台线程定时补充——避免额外线程与时钟精度问题，且天然线程安全
      （补充与消费共用同一把锁做同一次原子操作）。
    - **锁只保护"读时钟+算账"这一小段，真正的等待（sleep）在锁外发生**：否则
      多线程会退化为"排队串行 sleep"，白白拉长总耗时且掩盖并发正确性问题。
    """

    def __init__(
        self,
        rate_per_min: float,
        *,
        capacity: float | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
    ) -> None:
        if rate_per_min <= 0:
            raise ValueError(f"rate_per_min 必须为正数，实际为 {rate_per_min!r}")
        resolved_capacity = capacity if capacity is not None else rate_per_min
        if resolved_capacity <= 0:
            raise ValueError(f"capacity 必须为正数，实际为 {resolved_capacity!r}")

        self._rate_per_sec = rate_per_min / 60.0
        self._capacity = float(resolved_capacity)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._tokens = self._capacity
        self._last_refill = clock()

    def _refill_locked(self) -> None:
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_sec)
            self._last_refill = now

    def acquire(self, tokens: float = 1.0) -> float:
        """阻塞直到拿到 `tokens` 个令牌；返回本次调用实际等待的秒数（便于测试断言/埋点）。"""
        if tokens <= 0:
            raise ValueError(f"tokens 必须为正数，实际为 {tokens!r}")
        if tokens > self._capacity:
            raise ValueError(f"单次请求 {tokens} 个令牌超过桶容量 {self._capacity}，永远无法满足")

        total_waited = 0.0
        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return total_waited
                deficit = tokens - self._tokens
                wait_s = deficit / self._rate_per_sec
            self._sleep(wait_s)
            total_waited += wait_s

    def available_tokens(self) -> float:
        """当前可用令牌数（调试/单测用；会触发一次按时钟的补充结算）。"""
        with self._lock:
            self._refill_locked()
            return self._tokens


class RateLimiterRegistry:
    """按数据源名称持有独立 `TokenBucket` 的注册表。"""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def register(self, source: str, bucket: TokenBucket) -> None:
        """显式注册（或替换）某数据源的限流器。"""
        self._buckets[source] = bucket

    def get(self, source: str) -> TokenBucket:
        """取已注册的限流器；未注册时抛出清晰错误（而非静默创建，防止配置遗漏被掩盖）。"""
        try:
            return self._buckets[source]
        except KeyError as exc:
            raise KeyError(
                f"数据源 {source!r} 尚未注册限流器；请先调用 register() 或 get_or_create()"
            ) from exc

    def get_or_create(
        self,
        source: str,
        rate_per_min: float,
        *,
        capacity: float | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
    ) -> TokenBucket:
        """取已注册的限流器；不存在则按给定速率新建并注册。"""
        if source not in self._buckets:
            self._buckets[source] = TokenBucket(
                rate_per_min, capacity=capacity, clock=clock, sleep=sleep
            )
        return self._buckets[source]

    def sources(self) -> tuple[str, ...]:
        """已注册的数据源名称（调试/单测用）。"""
        return tuple(self._buckets.keys())
