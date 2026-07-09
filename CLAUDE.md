# CLAUDE.md — Tally（明账）项目铁律与开发约定

> 本文件是 `IMPLEMENTATION_SPEC.md`（唯一实现依据，single source of truth）的执行摘要。
> 两者冲突时，`IMPLEMENTATION_SPEC.md` 优先；任何实现不得违反本文件列出的铁律。
> 开发流程（多 Agent 六步闭环）见 `docs/DEVELOPMENT_PROCESS.md`。

## 三条铁律（任何改动不得违反）

1. **防未来函数**
   任何指标计算只能使用截断到 `as_of_date` 的数据；财务数据一律按其生效日
   （见 `common/market_profile.py` 的 `MarketProfile.fin_effective_rule`）之后可见。
   - CN：`announce_plus_1td`（公告日 + 1 交易日）
   - US 主源：`filed_plus_1td`（SEC `filed` + 1 交易日）
   - US fallback：`report_end_plus_45_60cd`（报告期末 + 45/60 自然日）

2. **回测与实盘同源**
   策略/风控/组合代码只有一份，回测引擎只负责喂"截断到 T 日"的数据切片。
   不允许为回测单独写一套策略/组合逻辑。

3. **所有持久化经 Repository**
   业务模块禁止裸 SQL；唯一 SQL 入口是 `data/repository.py`。

## 目录约定（IMPLEMENTATION_SPEC.md §2）

```
src/tally/
├── portfolio/    组合层：ledger.py(双账本) / allocator.py(裁决器) / gates.py(双闸门) / constraints.py
├── strategy/     Strategy 基类 + S1/S2/S4 + indicators.py + registry.py
├── pool/         入池/持仓维持标准，按 market 参数化
├── backtest/     engine.py / broker.py(按市场分支) / metrics.py
├── tracking/     信号追踪(模型口径) + 执行偏离(实际口径)
├── data/         repository.py(唯一 SQL 入口) / sync.py / derived.py / sources/
└── common/       config.py(pydantic) / calendar.py(按 market 分派) / market_profile.py / logging / notify(telegram)
config/           system.yaml / portfolio.yaml / strategies.yaml / pool.yaml / backtest.yaml
tests/
├── fixtures/     真实 API 响应录制回放（M1 起使用，见下）
├── golden/       黄金用例（逐笔人工核对的回测结果）
└── synth/        合成K线生成器（六形态，见 T0.5.5）
```

## "AC 全绿才能进入下一任务"

`IMPLEMENTATION_SPEC.md` §11 每个任务（T0.x/T1.x/…）都有可判定的验收标准（AC）。
任何任务的 AC 未全部满足，不得开始下一个任务；不满足时先修复或按 spec 里的
降级/归因规则处理，而不是绕过或弱化 AC。

## strategy/ 禁止 `if market ==`

`strategy/` 目录下代码（`market_profile.py` 与 `backtest/broker.py` 除外）**禁止出现
`if market ==` 字面判断**。市场差异只能通过两条通道表达：

1. **能力开关**：读 `ctx.profile`（`MarketProfile` 的 frozen 字段，如 `has_price_limit`）；
2. **参数差异**：读 `self.params`（已由 `strategy/registry.py` 用
   `strategies.yaml` 的 `market_overrides.<MARKET>` 深合并装配完成的终值）。

CI/单测用 AST 或 grep 检查 `strategy/` 目录，命中 `if market ==` 视为 CRITICAL，必须打回。

## 无魔法数字

任何可调的业务参数（阈值、比例、天数、窗口长度……）必须放进 `config/*.yaml`，
代码中不得出现裸露的业务常量。纯粹的算法实现细节常量（如本文档描述的合成数据
噪声 sigma 这类"生成器自身的固定实现细节，不是业务参数"）可以留在代码里，
但要有注释说明"为什么不是业务参数"。

## 密钥走 .env

`TUSHARE_TOKEN` / `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` 走项目根 `.env`
（已在 `.gitignore`，不入库；模板见 `.env.example`）。`config/*.yaml` 中以
`env:VAR` 语义引用，由 `common/config/base.py::resolve_env_ref` 在加载配置时解析。
SEC EDGAR 不需要 key，但必须设置合规 `User-Agent`（含联系邮箱），见
`config/system.yaml` 的 `sec_user_agent`。

## fixture 录制回放（M1 起，`tests/fixtures/`）

数据源适配器（`data/sources/*.py`）的单测不得在 CI 中打真实 API：

1. **录制**：本地设置好真实凭证（`.env`）后，以专门的录制脚本/标记跑一次真实调用，
   把响应（JSON/CSV 原始报文，去除敏感字段）落盘到 `tests/fixtures/<source>/<case>.json`。
2. **回放**：单测默认从 fixture 读取并 mock 网络层（`requests`/SDK 调用），不联网。
3. **更新**：数据源字段/契约变化时才重新录制，并在对应 PR 说明重新录制的原因。
4. CI 环境不装真实凭证；录制回放单测必须能在无 `.env` 情况下全绿。

## AC 全绿的证据留存

每个任务完成后，在对应 changelog（`docs/changelog/<YYYY-MM-DD>-<模块>.md`，
模板见 `docs/changelog/TEMPLATE.md`）里逐条列出 AC 与证据（测试名/命令输出），
供后续里程碑与产品经理角度回归验收时复核。
