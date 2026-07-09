"""深合并：strategies.yaml 的 market_overrides 语义（IMPLEMENTATION_SPEC.md §5.1.1/§10）。

语义：
- 逐 key 递归合并两个 dict；
- override 中某 key 的值为 None（yaml 的 `null`）→ 删除 base 中该 key（若存在）；
- override 中某 key 的值为 list → 整体替换 base 中的同名 list（不逐元素合并）；
- 其余标量值 → 直接覆盖。
不修改传入的 base/override（不可变数据风格）。
"""

from __future__ import annotations

from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """返回 base 与 override 深合并后的新 dict；不改动 base/override 本身。"""
    result: dict[str, Any] = dict(base)
    for key, value in override.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
