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
    """返回 base 与 override 深合并后的新 dict；不改动 base/override 本身。

    浅拷贝契约：只有沿着"两侧都是 dict"的路径才会递归产生新的嵌套 dict；
    其余值（包括 list/tuple 等非 dict 容器）在覆盖或保留时都是**按引用**带入
    结果的，不做深拷贝。对本项目的用法（yaml 解析出的即用即弃的临时结构，
    合并后立即喂给 `model_validate` 产出 frozen 模型）这一点无副作用，但
    调用方若打算在合并后继续原地修改 base/override 内部的可变容器，需自行
    深拷贝。
    """
    result: dict[str, Any] = dict(base)
    for key, value in override.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
