"""数据层：Repository（唯一 SQL 入口）、同步管道、派生指标计算。

对应 IMPLEMENTATION_SPEC.md §3。M0.5 阶段仅骨架，实现见 M1（T1.1/T1.2）与 M2/M3。

铁律（不得违反）：所有持久化经 Repository，业务模块禁止裸 SQL。
"""
