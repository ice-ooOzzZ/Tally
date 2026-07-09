"""铁律守护：strategy/ 目录禁止 `if market ==` 字面判断（CLAUDE.md / spec §5.1.1）。

用 AST 扫描 `src/tally/strategy/` 下所有 .py 文件，检测两类模式：
1. 比较表达式：`market == "CN"` / `"US" != market` / `market in ("CN", "US")`
   （`Eq`/`NotEq`/`In`/`NotIn`，字符串字面量既可以是裸常量，也可以出现在
   tuple/list/set 字面量里）；
2. `match market: case "CN": ...` 结构化匹配（`MatchValue`/`MatchOr`）。

**重要限定（尽力而为的静态 lint，不是完备证明）**：本 guard 是启发式 AST 扫描，
不做变量别名追踪、不做跨函数数据流分析、不识别 `getattr(ctx, "market")` 这类
动态取属性。也就是说下面几种写法**不会**被本 guard 捕获，仍需人工 code review
把关：
- 变量别名：`m = market` 之后 `if m == "CN":`；
- 动态属性：`getattr(ctx, "market") == "CN"`；
- 经过任意函数转换后的值：`normalize(market) == "cn"`。

`market_profile.py` 与 `backtest/broker.py` 按 spec 豁免（但两者均不在
`strategy/` 目录下，本 guard 只扫描 `strategy/`，天然不会误伤它们）。

M0.5 阶段 `strategy/` 仅有空 `__init__.py`，本测试主要为后续里程碑（S1/S2/S4
实现）提前建好门禁；`test_guard_detects_known_violation_patterns` 用内联违规
代码片段反向验证 guard 本身没有失效（正向的"当前 strategy/ 目录干净"测试无法
证明 guard 还在正常工作——它也会在 guard 彻底坏掉时"通过"）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

STRATEGY_DIR = Path(__file__).resolve().parents[1] / "src" / "tally" / "strategy"

_COMPARISON_OPS = (ast.Eq, ast.NotEq, ast.In, ast.NotIn)


def _is_market_name(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id.lower() == "market"
    if isinstance(node, ast.Attribute):
        return node.attr.lower() == "market"
    return False


def _string_literal_values(node: ast.expr) -> list[str]:
    """提取表达式里出现的字符串字面量，包括 tuple/list/set 字面量内的元素。

    这样 `market in ("CN", "US")` 也能被识别为"字符串字面量比较"，
    而不仅仅是 `market == "CN"` 这种最简单的形式。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        values: list[str] = []
        for elt in node.elts:
            values.extend(_string_literal_values(elt))
        return values
    return []


def _match_pattern_has_string_literal(pattern: ast.pattern) -> bool:
    """`match market: case "CN": ...` 里 case 子句是否命中字符串常量。"""
    if isinstance(pattern, ast.MatchValue):
        return isinstance(pattern.value, ast.Constant) and isinstance(pattern.value.value, str)
    if isinstance(pattern, ast.MatchOr):
        return any(_match_pattern_has_string_literal(p) for p in pattern.patterns)
    return False


def _find_market_literal_comparisons(tree: ast.AST) -> list[int]:
    """返回文件中所有 `market ==/!=/in/not in "XX"` 形式比较的行号。"""
    offending_lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        ops_are_relevant = all(isinstance(op, _COMPARISON_OPS) for op in node.ops)
        if not ops_are_relevant:
            continue
        has_market_name = any(_is_market_name(operand) for operand in operands)
        has_string_literal = any(_string_literal_values(operand) for operand in operands)
        if has_market_name and has_string_literal:
            offending_lines.append(node.lineno)
    return offending_lines


def _find_market_literal_matches(tree: ast.AST) -> list[int]:
    """返回文件中所有 `match market: case "XX": ...` 结构的行号。"""
    offending_lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Match):
            continue
        if not _is_market_name(node.subject):
            continue
        if any(_match_pattern_has_string_literal(case.pattern) for case in node.cases):
            offending_lines.append(node.lineno)
    return offending_lines


def _find_violations(tree: ast.AST) -> list[int]:
    """合并比较类与 match-case 类两种字面市场判断，返回排序后的行号列表。"""
    lines = _find_market_literal_comparisons(tree) + _find_market_literal_matches(tree)
    return sorted(lines)


def test_strategy_dir_has_no_market_literal_comparison() -> None:
    if not STRATEGY_DIR.is_dir():
        return  # 目录尚未创建（不应发生，但不让本测试变成 collection 失败）

    violations: dict[str, list[int]] = {}
    for py_file in STRATEGY_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        lines = _find_violations(tree)
        if lines:
            violations[str(py_file.relative_to(STRATEGY_DIR))] = lines

    assert not violations, f"strategy/ 中发现 `if market ==` 字面判断：{violations}"


# ---- 反向测试：guard 自身是否还能识别已知的违规写法 -----------------------------
#
# 上面那条测试只验证"当前 strategy/ 目录是干净的"，这个结论在 guard 逻辑
# 被误改坏（例如 _COMPARISON_OPS 被误删、_is_market_name 拼错）时也会一样
# "通过"，从而静默失效。下面用内联违规代码片段反向验证 guard 确实能标记出
# 每一类已知违规写法。

_VIOLATING_SNIPPETS: dict[str, str] = {
    "eq_market_left": 'def f(market):\n    if market == "CN":\n        pass\n',
    "eq_market_right": 'def f(market):\n    if "US" == market:\n        pass\n',
    "noteq": 'def f(market):\n    if market != "CN":\n        pass\n',
    "in_tuple": 'def f(market):\n    if market in ("CN", "US"):\n        pass\n',
    "not_in_list": 'def f(market):\n    if market not in ["CN"]:\n        pass\n',
    "attribute": 'def f(ctx):\n    if ctx.market == "CN":\n        pass\n',
    "match_case": 'def f(market):\n    match market:\n        case "CN":\n            pass\n',
    "match_case_or": (
        'def f(market):\n    match market:\n        case "CN" | "US":\n            pass\n'
    ),
}


@pytest.mark.parametrize("snippet", _VIOLATING_SNIPPETS.values(), ids=list(_VIOLATING_SNIPPETS))
def test_guard_detects_known_violation_patterns(snippet: str) -> None:
    tree = ast.parse(snippet)
    assert _find_violations(tree), f"guard 未能检测出已知违规写法：{snippet!r}"


_CLEAN_SNIPPETS: dict[str, str] = {
    "capability_flag": "def f(ctx):\n    if ctx.profile.has_price_limit:\n        pass\n",
    "params_only": "def f(self):\n    if self.params.entry.ma_gate > 0:\n        pass\n",
    "unrelated_string_eq": 'def f(status):\n    if status == "CN":\n        pass\n',
}


@pytest.mark.parametrize("snippet", _CLEAN_SNIPPETS.values(), ids=list(_CLEAN_SNIPPETS))
def test_guard_does_not_flag_clean_patterns(snippet: str) -> None:
    """负向对照：能力开关/参数读取，以及"字符串巧合等于市场码但变量名不是
    market"的写法不应被误报（`unrelated_string_eq` 故意用同样的字符串常量
    "CN"，验证 guard 是按变量名 `market` 而不是按字符串值判断）。"""
    tree = ast.parse(snippet)
    assert not _find_violations(tree), f"guard 误报了本应合法的写法：{snippet!r}"
