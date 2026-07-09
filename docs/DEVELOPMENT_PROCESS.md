# Tally 开发流程(多 Agent 流水线)

> 本文件定义 Tally 每一个模块改动从编码到合入 `main` 的标准流程。任何改动不得跳过流水线。

## 集成分支

- 集成分支 = `main`。
- 每个模块/任务走独立 feature 分支:`feat/<里程碑>-<模块>`(如 `feat/m1-repository`),完成后合入 `main`。

## 每个改动的流水线(6 步闭环)

```
① 实现     开发 Agent 在 feature 分支写代码(worktree 隔离)
              ↓
② 代码审查  2 个资深 Agent 并行 review:code-reviewer + python-reviewer
              ↓ (CRITICAL/HIGH 未清零 → 打回 ①)
③ 测试     2 个测试 Agent 并行:
              - tdd-guide:单元/集成测试,验证 80%+ 覆盖
              - 测试工程师:AC 验证 + 防未来函数 + 边界/合成用例
              ↓ (AC 未全绿 → 打回 ①)
④ 改动文档  写入 docs/changelog/<YYYY-MM-DD>-<模块>.md(合并前必写,模板见 changelog/TEMPLATE.md)
              ↓
⑤ 合并     feature 分支合入 main
              ↓
⑥ 回归     1 测试 Agent(全量回归)+ 1 产品经理 Agent(产品/AC 角度验收)
```

## 硬性门禁(引用 IMPLEMENTATION_SPEC.md)

- **AC 全绿才能进入下一任务**(spec §0)。
- 三条铁律任何改动不得违反:防未来函数 / 回测实盘同源 / 所有持久化经 Repository(spec §0)。
- 策略 `strategy/` 目录禁止 `if market ==` 字面判断(spec §5.1.1,AST/grep 单测检查)。
- 无魔法数字:可调参数进 `config/*.yaml`。
- 密钥走 `.env`,不入库。

## 角色与 Agent 映射

| 步骤 | 角色 | Agent 类型 |
|---|---|---|
| ① 实现 | 开发 | general-purpose / software-engineer |
| ② 审查 | 资深审查 ×2 | code-reviewer + python-reviewer |
| ③ 测试 | 测试 ×2 | tdd-guide + 测试工程师(general-purpose) |
| ⑥ 回归 | 测试 + 产品 | 测试 Agent + 产品经理 Agent |

## 里程碑顺序(spec §11)

M0(数据 spike,需凭证,暂缓)→ **M0.5 工程脚手架** → M1 垂直切片(A股 S1)→ M2 数据层完备+回测+S1 过门 → M3 S2/S4+美股接入+组合决策门 → M4 组合宪法完备+日常闭环 → M5 试运行。
