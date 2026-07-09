"""策略层：Strategy 基类、S1/S2/S4 三条策略、指标、注册与配置装配。

对应 IMPLEMENTATION_SPEC.md §5。M0.5 阶段仅骨架。

铁律（不得违反，见 CLAUDE.md）：本目录下代码禁止出现 `if market ==` 字面判断，
市场差异只能通过 MarketProfile 能力开关与 strategies.yaml 的 market_overrides
深合并表达（market_profile.py 与 broker.py 除外）。
"""
