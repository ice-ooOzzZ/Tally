"""铁律守护：strategy/ 目录禁止 `if market ==` 字面判断（CLAUDE.md / spec §5.1.1）。

用 AST 扫描 `src/tally/strategy/` 下所有 .py 文件的 If 节点，检测比较表达式左右两侧是否
任一为名为 `market`（大小写不敏感）的变量/属性名与字符串常量的相等/不等比较。
`market_profile.py` 与 `backtest/broker.py` 按 spec 豁免（但目前不在 strategy/ 目录下）。

M0.5 阶段 strategy/ 仅有空 `__init__.py`，本测试主要为后续里程碑（S1/S2/S4 实现）
提前建好门禁，防止未来引入字面市场判断。
"""

from __future__ import annotations

import ast
from pathlib import Path

STRATEGY_DIR = Path(__file__).resolve().parents[1] / "src" / "tally" / "strategy"


def _is_market_name(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id.lower() == "market"
    if isinstance(node, ast.Attribute):
        return node.attr.lower() == "market"
    return False


def _is_string_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _find_market_literal_comparisons(tree: ast.AST) -> list[int]:
    """返回文件中所有 `market == "XX"` / `"XX" == market` 形式比较的行号。"""
    offending_lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        ops_are_eq = all(isinstance(op, ast.Eq | ast.NotEq) for op in node.ops)
        if not ops_are_eq:
            continue
        has_market_name = any(_is_market_name(operand) for operand in operands)
        has_string_literal = any(_is_string_literal(operand) for operand in operands)
        if has_market_name and has_string_literal:
            offending_lines.append(node.lineno)
    return offending_lines


def test_strategy_dir_has_no_market_literal_comparison() -> None:
    if not STRATEGY_DIR.is_dir():
        return  # 目录尚未创建（不应发生，但不让本测试变成 collection 失败）

    violations: dict[str, list[int]] = {}
    for py_file in STRATEGY_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        lines = _find_market_literal_comparisons(tree)
        if lines:
            violations[str(py_file.relative_to(STRATEGY_DIR))] = lines

    assert not violations, f"strategy/ 中发现 `if market ==` 字面判断：{violations}"
