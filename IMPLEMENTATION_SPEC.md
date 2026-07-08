# Tally（明账）· 实现规格文档（Implementation Spec）

> 项目名：**Tally**（中文名：明账）｜ repo：`tally` ｜ CLI 命令：`tally`
> 命名由来：tally stick（符木）——金融史上最早的防篡改双账本，对应本系统"模型账本 vs 真实持仓"的双账本设计；"明账" = 每条建议明明白白记账、可追溯、可证伪。
> 版本 v2.0-final ｜ 2026-07-08 ｜ 面向 Claude Code 的工程实现文档
> 演进链：PRD v1.0（量化+架构双审）→ 策略共识（三研究员辩论）→ 金融复审（组合风控总监+数据执行专家）→ 架构重定义 → **PM 评审后修订（本版）**
> 本文档为唯一实现依据（single source of truth），信息自足，不依赖任何外部文档。

---

## 0. 给实现者（Claude Code）的说明

- 按 §11 里程碑顺序实现。每个任务有可判定的验收标准（AC），**AC 全绿才能进入下一任务**。
- 三条铁律（写入 CLAUDE.md，任何实现不得违反）：
  1. **防未来函数**：任何指标计算只能使用截断到 `as_of_date` 的数据；财务数据一律按 `announce_date + 1 交易日` 生效。
  2. **回测与实盘同源**：策略/风控/组合代码只有一份，回测引擎只负责喂"截断到 T 日"的数据切片。
  3. **所有持久化经 Repository**：业务模块禁止裸 SQL。
- 所有可调参数放 `config/*.yaml`，代码中不得出现魔法数字。
- 技术栈：Python 3.11+；pandas、numpy、pydantic、typer（CLI）、streamlit、plotly、tushare、akshare、pytest。
- 密钥：`TUSHARE_TOKEN`、`SLACK_WEBHOOK` 走 `.env`（入 `.gitignore`），config 中以 `env:VAR` 引用。
- 数据库演进：一期允许"删库重同步"（数据均可从源重建），DDL 变更无需迁移脚本，但须同步更新本文档 §3.2。

## 1. 系统目标与一期范围

每周按基本面标准维护股票池；每日对池内股票运行策略引擎，输出可解释的买入/卖出建议（原因、止损价、仓位）；用户通过看板/CLI 回填实际成交；系统持续追踪建议表现；策略上线前必须通过历史回测决策门。

**一期范围（PM 评审后收敛）**：**A股单市场 + 三条策略（S1 趋势突破 / S2 事件PEAD / S4 恐慌回补）+ 双闸门 + 看板核心三页**。

**明确移入二期**（附录 A 保留完整规格，供二期直接实现）：
- 美股腿（yfinance 数据链路最脆弱、降级路径最多，投入产出比最低）
- S3 质量折价、S5 盈利动量（长持策略，试运行期内积累不出可评估样本；且美股腿数据不可得）
- 看板③个股详情、④成绩单详情页的图表增强、⑥回测实验室（CLI 已能跑回测）
- 问询函自动监控（降级为人工黑名单）、A+美混合净值、汇率、自动下单、ML、多用户

## 2. 总体架构

```
┌─ 入口A: tally CLI（幂等 catch-up runner，as_of_date 贯穿全链路）
├─ 入口B: dashboard（Streamlit，一期三页）
│
├─ portfolio/                组合层（一等模块）
│   ├─ ledger.py             双账本：模型账本 + 实际账本
│   ├─ allocator.py          软配额 + 现金争用裁决器
│   ├─ gates.py              MarketRegimeGate + PortfolioDrawdownGate
│   └─ constraints.py        单票/行业/同票合并上限（权重口径）
├─ strategy/
│   ├─ base.py               Strategy 基类 + 持仓状态机
│   ├─ s1_breakout.py / s2_pead.py / s4_panic_reversion.py
│   ├─ indicators.py         技术指标（动态复权）
│   └─ registry.py           策略注册与配置装配
├─ pool/screener.py          入池标准 与 持仓维持标准 分离
├─ backtest/
│   ├─ engine.py             单策略回测 + 组合合成回测（同一撮合器）
│   ├─ broker.py             T+1/停牌/涨跌停/分档滑点/税费
│   └─ metrics.py            绩效 + 单规则归因 + 策略相关矩阵
├─ tracking/                 信号追踪（模型口径）+ 执行偏离（实际口径）
├─ data/
│   ├─ sources/              tushare_source.py（主源）/ akshare_source.py（辅源+降级）
│   ├─ repository.py         唯一 SQL 入口
│   ├─ sync.py               全局令牌桶 / 单写线程 / sync_failures 补拉
│   └─ derived.py            派生指标：roe_ttm、rev_yoy_q、估值分位
├─ common/                   config(pydantic) / calendar / logging / notify(slack)
└─ tests/                    fixtures/（录制回放）+ golden/（黄金用例）+ synth/（合成K线生成器）
```

**架构决策记录（ADR）**：

| # | 决策 | 依据 |
|---|---|---|
| ADR-1 | Tushare Pro 为 A股行情+财务+估值主源；AkShare 为辅源与降级通道 | 数据复审：18 项字段缺口中 11 项唯 Tushare 稳定提供 |
| ADR-2 | 市场资金池完全独立封闭（一期仅 A股，接口按多市场设计） | 双券商+外汇管制的物理现实 |
| ADR-3 | 双账本：模型账本（假设全执行，度量策略）+ 实际账本（用户回填，驱动风控） | 建议式系统的账本漂移问题 |
| ADR-4 | 组合模型为"软配额+统一现金池"；废除早期版本的 2% 风险仓位公式与 15只/10% 单账本 | 消解组合模型矛盾 |
| ADR-5 | 出池仅禁新开仓，存量按策略自身规则退出（ST/退市/停牌超60日除外） | 防止池规则截断赢家右尾 |
| ADR-6 | PE 口径统一 PE_ttm（Tushare daily_basic），"东财动态PE"仅展示 | 动态PE无历史序列不可回测 |
| ADR-7 | 一期砍美股与 S3/S5，与风险预案对齐 | PM 评审：反馈周期与数据脆弱性 |

## 3. 数据层规格

### 3.1 数据源矩阵（一期 A股部分，M0 逐项验证）

| # | 数据 | 主源（接口） | 降级 | 频率 |
|---|---|---|---|---|
| 1 | 全市场日频行情+成交额 | Tushare `daily`（按 trade_date 批量，20日=20次调用） | AkShare 单票循环（仅池内） | 每交易日 |
| 2 | 池内日K+复权因子 | Tushare `daily` + `adj_factor` | AkShare `stock_zh_a_hist` | 每交易日 |
| 3 | 日频估值 pe_ttm/pb/市值（含 3 年回填） | Tushare `daily_basic` | 缺失则估值条件停用并标注 | 每交易日 |
| 4 | 财务原始报表（归母净利/净资产/营收/OCF 累计值，8期+，含 ann_date） | Tushare `income`/`balancesheet`/`cashflow` | AkShare 新浪三大报表 | 每季 |
| 5 | 单季营收同比 | Tushare `fina_indicator.q_sales_yoy` | 自行差分累计值（重述：用当期披露的同期数） | 每季 |
| 6 | 业绩预告/快报（含公告日） | Tushare `forecast`/`express` | AkShare `stock_yjyg_em`/`stock_yjkb_em` | 每日增量 |
| 7 | 预约披露时间表 | AkShare 巨潮预约披露接口 | 财报季（1/4/7/10月）整月扩大回避半径 | 每周 |
| 8 | 涨跌停价 | Tushare `stk_limit` | 板块规则推断（主板±10%/创科±20%/ST±5%） | 每交易日 |
| 9 | 行业分类（申万一级，含 in_date/out_date） | Tushare `index_classify`+`index_member` | AkShare 东财板块（口径注明） | 每月 |
| 10 | 沪深300全收益 H00300 | AkShare `stock_zh_index_hist_csindex` | 510300 ETF 后复权 → 000300+年化2.2%修正 | 每交易日 |
| 11 | 交易日历 | Tushare `trade_cal` | AkShare 交易日历 | 每年 |

### 3.2 存储 DDL（SQLite，WAL + busy_timeout，Repository 收口）

```sql
CREATE TABLE kline (                    -- 指数复用本表, market ∈ {CN, INDEX}
  code TEXT, market TEXT, date TEXT,    -- date = 交易所当地交易日 YYYY-MM-DD
  open REAL, high REAL, low REAL, close REAL,
  volume REAL, amount REAL, adj_factor REAL,   -- 不复权价+复权因子, 计算时动态复权
  PRIMARY KEY (code, market, date));
CREATE INDEX idx_kline_md ON kline(market, date);

CREATE TABLE valuation (
  code TEXT, market TEXT, date TEXT,
  pe_ttm REAL, pb REAL, market_cap REAL, turnover_amt REAL,
  PRIMARY KEY (code, market, date));

CREATE TABLE limit_prices (
  code TEXT, market TEXT, date TEXT, up_limit REAL, down_limit REAL,
  PRIMARY KEY (code, market, date));

CREATE TABLE fundamentals_raw (
  code TEXT, market TEXT, report_date TEXT, announce_date TEXT,
  revenue_ytd REAL, net_profit_ytd REAL, equity REAL, ocf_ytd REAL,
  PRIMARY KEY (code, market, report_date));

CREATE TABLE fundamentals_derived (     -- derived.py 计算后缓存
  code TEXT, market TEXT, report_date TEXT, announce_date TEXT,
  roe_ttm REAL, rev_yoy_q REAL, ocf_q REAL, audit_opinion TEXT,
  PRIMARY KEY (code, market, report_date));

CREATE TABLE earnings_events (          -- 预告/快报/正式披露
  code TEXT, market TEXT, ann_date TEXT,
  event_type TEXT,                      -- forecast / express / report / scheduled(预约)
  period TEXT, summary_json TEXT,
  PRIMARY KEY (code, market, ann_date, event_type));

CREATE TABLE snapshot (                 -- 周度快照, 自建 PIT
  trade_date TEXT, code TEXT, market TEXT,
  pe_ttm REAL, pb REAL, market_cap REAL, amount_20d_avg REAL,
  is_st INTEGER, list_date TEXT, industry TEXT,
  PRIMARY KEY (trade_date, code, market));

CREATE TABLE pool_history (
  code TEXT, market TEXT, action TEXT,  -- enter/exit/blacklist/whitelist
  date TEXT, reason TEXT);
CREATE INDEX idx_pool_date ON pool_history(date);

CREATE TABLE signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id TEXT, date TEXT, code TEXT, market TEXT,
  advice TEXT,                          -- buy / exit / avoid / hold
  score REAL, reasons_json TEXT,
  price_at_signal REAL, stop_loss REAL, position_pct REAL);
CREATE INDEX idx_signals_date ON signals(date);

CREATE TABLE positions (                -- 分策略持仓批次, ledger ∈ {model, actual}
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ledger TEXT, strategy_id TEXT, code TEXT, market TEXT,
  entry_date TEXT, entry_price REAL, weight_pct REAL,   -- 分母=市场子组合净值
  status TEXT,                          -- open / closed
  exit_date TEXT, exit_price REAL, exit_reason TEXT,
  signal_id INTEGER REFERENCES signals(id));

CREATE TABLE executions (               -- 用户成交回填: 每 signal 一条, 回填即覆盖
  signal_id INTEGER PRIMARY KEY REFERENCES signals(id),
  status TEXT NOT NULL DEFAULT 'skipped',   -- filled / partial / skipped
  fill_price REAL, fill_weight_pct REAL, fill_date TEXT, note TEXT);

CREATE TABLE portfolio_nav (
  date TEXT, market TEXT, ledger TEXT,
  nav REAL, cash_pct REAL, dd_from_peak REAL, regime TEXT,
  PRIMARY KEY (date, market, ledger));

CREATE TABLE signal_tracking (
  signal_id INTEGER, days_after INTEGER,        -- 1/5/20
  adj_return_pct REAL, benchmark_return_pct REAL,
  PRIMARY KEY (signal_id, days_after));

CREATE TABLE sync_failures (code TEXT, market TEXT, task TEXT, fail_date TEXT, retry_count INTEGER);

CREATE TABLE backtest_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_config_json TEXT, code_version TEXT, period TEXT,
  is_oos INTEGER DEFAULT 0,             -- 是否动用了样本外区间
  metrics_json TEXT, created_at TEXT);
-- 样本外查看预算: CLI 统计 is_oos=1 的 run 数, ≥3 时拒跑,
-- 需显式 --exceed-oos-budget 并在报告首页标注超支
```

### 3.3 同步管道要求

全局令牌桶（Tushare 按积分配额、AkShare 并发 1–2）；多线程拉取单线程批量写；增量按缺失区间；失败入 `sync_failures` 下次优先补拉，连续 3 天失败 Slack 告警；K线覆盖率缺最近 3 日的股票当日跳过信号并在报告标注；每任务记录行数/时长基线，偏离 3 倍告警。

## 4. 组合层规格（资金宪法）

### 4.1 资金模型（一期 A股三策略版）

- 仓位百分比分母一律为 **A股子组合当前净值**。
- 软配额 + 统一现金池：

| 策略 | S1 趋势突破 | S2 事件PEAD | S4 恐慌回补 | 现金底仓 |
|---|---|---|---|---|
| 目标配额 | 40% | 30% | 20% | 10% |
| 漂移带 | ±10pp | ±10pp | ±5pp | — |

- 闲置额度自动回归现金池，可被其他策略在其配额上限（目标+漂移带）内借用；现金按 `cash_yield`（默认 1.8%/年）计息。
- **总仓上限为最高优先约束**：双闸门给出上限后，各策略配额按比例缩放。
- 单票上限 8%（S4 为 5%）；同票多策略持有合并计仓 ≤ 8%；单申万一级行业持仓权重 ≤ 25%（与池的数量口径无关）。

### 4.2 现金争用裁决器（allocator.py）

同日多信号资金不足时：① 退出信号无条件优先；② 买入按"策略配额剩余空间大者优先，同级按策略回测单位风险收益排序"；③ 排不进的信号放弃不排队；④ 禁止为新信号平掉其他策略存量；⑤ 同票同日先卖后买禁止。**同一套裁决器用于回测撮合与实盘建议。**

### 4.3 双闸门（gates.py）

**MarketRegimeGate**（完整规则，内联自策略共识）：

```
输入: 基准指数(H00300)日K, 每日收盘计算, T+1 生效
trend = index_close > SMA(index_close, 200)
mom   = index_close / index_close[t-60] − 1
vol   = std(index_daily_return, 20) × √252
dd_ix = index_close / max(index_close, 近250日) − 1

熊市   = (NOT trend AND mom < −0.05) OR dd_ix < −0.20    # dd 是独立熔断条款
牛市   = trend AND mom ≥ 0.03 AND vol ≤ 0.30             # A股 vol 阈值 0.30, 写死禁改
震荡市 = 其余
防抖: 状态切换需连续 5 个交易日满足新状态才正式切换;
     防抖确认期间(疑似切换期)冻结全部新开仓
输出总仓上限: 牛 80% / 震荡 60% / 熊 25%(仅短持类;一期三策略均为短持类)
熊市附加: S4 时间止损 7日→3日; 降档超限时按浮亏从大到小、非跌停日限价减仓
```

**PortfolioDrawdownGate**（基于**实际账本**净值）：`dd ≤ −10%` → 新开仓额度减半；`dd ≤ −15%` → 禁止一切新开仓；恢复至 `−8%` 以内解除。与 RegimeGate 取更严者。

**账本失真防护**（PM 评审新增）：存在超过 3 个交易日未确认（status=skipped 且未过期）的信号时，双闸门与止损锚定自动降级为**模型账本口径**，日报醒目提示"实际账本不可信，请回填"。

### 4.4 双账本（ledger.py）

- 模型账本：假设每条建议按 T+1 开盘全额执行。用途：信号追踪、策略评估、与回测对齐。
- 实际账本：以 `executions` 为准，默认 skipped。用途：持仓卖出信号对象判定、止损价锚定（真实成交价）、双闸门、集中度约束。
- 偏离度指标：建议执行率、平均执行滑点；连续 10 个交易日执行率 < 50% → Slack 提示"建议与实际操作严重脱节"。

## 5. 策略层规格

### 5.1 Strategy 基类与状态机

```python
class Strategy(ABC):
    id: str                    # s1 / s2 / s4
    markets: list[str]         # 一期均为 ["CN"]
    holding_style: str         # short / long

    @abstractmethod
    def scan_entries(self, ctx: MarketContext) -> list[EntrySignal]: ...
    @abstractmethod
    def check_exits(self, positions: list[Position], ctx: MarketContext) -> list[ExitSignal]: ...
    # ctx 只含截断到 as_of_date 的数据视图, 由引擎保证; 策略内部禁止访问 Repository

# 持仓生命周期(共用): 入场 → 持有 → 退出(四者先到):
# ① 策略退出信号 ② 止损触发 ③ 恶性事件强平(ST/退市/停牌>60日) ④ 策略最长持有期
# 出池不强平(ADR-5), 仅禁新开; 出池持仓股数据继续同步
```

### 5.2 三条策略精确规格（参数落 `config/strategies.yaml`）

**S1 趋势突破**（short）
- 入场（T 日收盘全部满足，T+1 开盘执行）：
  - `close ≥ max(high[-250:-1])`（250日新高突破）
  - `volume ≥ 1.5 × mean(volume[-20:-1])`（放量确认）
  - `close > SMA50`
  - 泡沫刹车：池内 20 日新高股票占比 > 40% 时暂停新开仓
  - 剔除当日 `close ≥ up_limit × 0.99` 的股票（涨停/近涨停，用 limit_prices 精确判定）
  - 并发排序：多信号同日按 12-1 动量分 `close[-21]/close[-252] − 1` 降序
- 退出：吊灯止损 `close < max(high[entry..T-1]) − 3×ATR20`（水位截至 T−1，防未来函数）；连续 3 日 `close < SMA50`；硬止损 −8%。
- 上线验收：样本外区间按"次日开盘价成交 + 0.8% 滑点"回测，年化超额（对 H00300 全收益）> 0；量能倍数 {1.3,1.5,1.8}、ATR 倍数 {2.5,3,3.5}、硬止损 {−6%,−8%,−10%} 敏感性网格内期望同向。

**S2 事件PEAD**（short）
- 事件日 R：`earnings_events` 中该股最新事件（forecast/express/report），若事件日为非交易日或盘后则顺延次一交易日。同一 period 多次事件（预告→正式）各自独立判定，先触发先用。
- 基本面腿（按 announce_date+1 生效）：`rev_yoy_q > prev_rev_yoy_q` 且 `rev_yoy_q > 0`（**单季口径**）；`roe_ttm ≥ 8%`；`ocf_q > 0`。
- 价格腿：R 日涨幅 ≥ +3% 且 `volume[R] ≥ 2.0 × mean(volume[R-20:R-1])`；R 后 5 个交易日内 `close ≥ high[R]` 触发买入；`close > SMA200`。超 R+5 作废不追。
- 退出：45 交易日时间退出（敏感性 {30,45,60}）；新财报证伪（`rev_yoy_q < 0` 或 `ocf_q < 0`）→ announce_date+1 清仓；硬止损 −12%；下一已知披露日（预约披露表）前 1 交易日浮盈 < 5% 退出。

**S4 恐慌错杀回补**（short，单票 5%）
- 入场（全部满足）：
  - 活人判定：`close > SMA200` 且 `SMA200[T] > SMA200[T-20]`
  - 挨刀判定：`RSI3 < 15`；`close < SMA5`；`close/close[-5] − 1 < −6%`
  - 崩溃排除：近 5 日无跌停收盘（`close ≤ down_limit×1.01`）且当日非跌停收盘；`mean(amount[-5:]) / mean(amount[-60:]) < 3.0`
  - 健康检查：最新已披露 `roe_ttm > 0` 且 `ocf_q > 0`
  - 事件回避：距下一已知披露日 > 5 交易日（预约披露不可得时：1/4/7/10 月内回避半径扩大为 10 交易日）
- 退出（优先级顺序）：`RSI3 > 60`；时间止损 7 交易日（熊市 regime 减为 3）；硬止损 −7%；`close < SMA200`。跌停无法成交时次日续挂。
- 军令状（上线验收）：RSI3 阈值 {10,12,15,18,20} × 累跌 {−5%,−6%,−8%} 全网格期望同向且为正，任一翻转**整条废弃**；追加"次日低开 >5% 按开盘价止损"的现实撮合复测。

### 5.3 预期信号密度（干跑校准基线，T2.6 验证）

| 策略 | 预期密度（80 只池） | 过严/过松预警线 |
|---|---|---|
| S1 | 趋势市 0.2–0.8 条/日，震荡市近 0 | 连续 60 交易日 0 条 或 >3 条/日 |
| S2 | 财报季 0.3–1 条/日，非财报季近 0 | 财报季整季 <3 条 或 >5 条/日 |
| S4 | 震荡市 0.2–0.6 条/日 | 连续 60 交易日 0 条 或 >4 条/日 |

## 6. 股票池层规格

- **入池标准**（每周一）：市值 ≥100亿、`0 < pe_ttm ≤ 40`、`0 < pb < 8`、20日日均成交额 ≥1亿（全市场日频行情算得）、上市满 1 年、非 ST。满足者按 20 日日均成交额降序，单申万一级行业 ≤ 池内数量 20%，取前 50。
- **持仓维持标准**（与入池分离）：仅 ST/退市/停牌 >60 日强平；出池股票继续同步至所有策略退出。
- 出池宽限 2 周（连续 2 次周检不满足才出）；白名单永不出池 / 黑名单永不入池（人工维护，问询函走黑名单）。

## 7. 回测层规格

- **单策略回测**：该策略独占 100% 配额，输出绩效 + 单规则归因 + 参数敏感性。
- **组合合成回测**：完整 §4 组合宪法（同一份代码）。
- **broker.py**：T+1（当日买不可当日卖）；停牌禁成交持仓冻结；开盘即封板（一字板）不可成交；跌停日卖单顺延次日；滑点分档 S1/S4 0.8%、S2 0.2%；佣金双边 0.05%（最低 5 元）；印花税卖出侧分段（2023-08-28 前 0.1%，之后 0.05%）；持股 <1 月红利税 20%（撞除息日时）。
- **防过拟合**：训练段 2019–2023 / 样本外 2024 至今；样本外查看预算 3 次（backtest_runs.is_oos 强制计数，超支需 `--exceed-oos-budget` 且报告标注）；每 run 记 git commit。
- **组合级验收**：组合样本外最大回撤 < 最深单策略回撤；组合月度收益与任一单策略相关 < 0.9；三策略日收益两两相关 < 0.7（超过视为伪分散触发降配）；2018 段专项报告；DD 闸门有效性——2018 段熔断版回撤 ≤ 无熔断版 75%。
- **策略停用规则**（试运行期）：滚动 30 信号胜率 < 回测样本外胜率 − 2σ → 停用并复盘。

## 8. 追踪、报告与日常操作

### 8.1 信号追踪（模型账本口径）
T+1/T+5/T+20 复权收益 vs 基准；主口径"T+20 跑赢 H00300 全收益"；buy 与 avoid 分开统计；hold（观望）为对照组。

### 8.2 执行偏离（实际账本口径）
执行率、执行滑点，周报输出。

### 8.3 日报内容（Slack 摘要 + 看板详情）
今日信号（按策略分组：股票/原因列表/止损价/建议仓位）、待回填提醒、regime 状态与总仓上限、组合 dd（双账本）、数据健康摘要。

### 8.4 告警（三类）
任务最终失败；数据源连续异常/基线偏离；执行率过低或账本降级。

### 8.5 日常操作时刻表（PM 评审新增）

| 时点（北京时间） | 动作 | 主体 |
|---|---|---|
| 交易日 16:30 | launchd 触发 `tally run --until today`：同步→筛池(周一)→信号→追踪→报告→Slack | 系统 |
| 当晚任意时间 | 阅读 Slack 摘要（≤3 分钟）；打开看板或用 CLI 回填昨日成交 | 用户 |
| 次日 9:15–9:25 | 按建议在券商 App 挂集合竞价单（日报附建议限价） | 用户 |
| 电脑未开机 | launchd 错过后开机自动补触发；信号有效期 1 个交易日，隔日未执行自动作废（executions 标记 expired 语义 = skipped） | 系统 |

## 9. 看板（Streamlit，一期三页）

- **页① 今日信号与回填**：信号卡片 + filled/partial/skipped 三态回填（写 executions，回填即覆盖）。AC：单条信号回填 ≤3 次交互。另提供 CLI 快捷通道：`tally fill <signal_id> --price 12.3 --pct 5`。
- **页② 组合总览**：双账本净值曲线、dd、regime 历史、策略配额占用、行业集中度、同票重叠。
- **页⑤ 策略成绩单**：分策略滚动胜率（跑赢基准口径）、T+5/T+20 平均超额、信号组 vs 观望组、执行偏离趋势。

## 10. 配置文件规格

```yaml
# config/system.yaml
market: CN
initial_capital: 500000
benchmark: H00300            # 降级链: H00300 -> 510300_adj -> 000300+2.2%
cash_yield: 0.018
tushare_token: env:TUSHARE_TOKEN
slack_webhook: env:SLACK_WEBHOOK
rate_limits: {tushare_per_min: 400, akshare_concurrency: 2, akshare_interval_s: 0.5}

# config/portfolio.yaml
quotas: {s1: 0.40, s2: 0.30, s4: 0.20}      # 现金底仓 0.10
drift_band: {s1: 0.10, s2: 0.10, s4: 0.05}
per_stock_cap: {default: 0.08, s4: 0.05}
industry_cap_weight: 0.25
regime: {bull_exposure: 0.80, range_exposure: 0.60, bear_exposure: 0.25,
         sma_n: 200, mom_n: 60, mom_bear: -0.05, mom_bull: 0.03,
         vol_n: 20, vol_bull_max: 0.30, dd_bear: -0.20, confirm_days: 5}
dd_gate: {half: -0.10, freeze: -0.15, release: -0.08}
ledger_guard: {unconfirmed_days: 3, exec_rate_alert: 0.50, exec_rate_window: 10}

# config/strategies.yaml —— S1 为完整范式, S2/S4 依同构展开
s1_breakout:
  enabled: true
  entry:
    high_lookback: 250
    vol_mult: 1.5
    vol_lookback: 20
    ma_gate: 50
    bubble_brake_ratio: 0.40
    limit_up_exclude_ratio: 0.99
  exit:
    chandelier_atr_n: 20
    chandelier_atr_mult: 3.0
    ma_break_n: 50
    ma_break_days: 3
    hard_stop: -0.08
  ranking: {mom_skip: 21, mom_lookback: 252}
s2_pead:
  enabled: true
  entry: {r_jump_min: 0.03, r_vol_mult: 2.0, confirm_window: 5,
          roe_ttm_min: 0.08, ma_gate: 200}
  exit: {time_days: 45, hard_stop: -0.12, pre_report_min_profit: 0.05}
s4_panic_reversion:
  enabled: true
  entry: {rsi_n: 3, rsi_oversold: 15, drop_5d: -0.06, ma_trend: 200,
          ma_trend_rising_n: 20, ma_short: 5, vol_spike_max: 3.0,
          event_avoid_days: 5, event_avoid_days_fallback: 10}
  exit: {rsi_exit: 60, time_days: 7, time_days_bear: 3, hard_stop: -0.07}

# config/pool.yaml
entry: {min_mcap: 1.0e10, pe_ttm_range: [0, 40], pb_range: [0, 8],
        min_amount_20d: 1.0e8, min_list_days: 250, exclude_st: true}
size: 50
industry_cap_count: 0.20
exit_grace_weeks: 2

# config/backtest.yaml
train: {start: "2019-01-01", end: "2023-12-31"}
oos: {start: "2024-01-01", view_budget: 3}
costs: {commission: 0.0005, commission_min: 5,
        stamp_duty: [{until: "2023-08-27", rate: 0.001}, {from: "2023-08-28", rate: 0.0005}],
        slippage: {s1: 0.008, s2: 0.002, s4: 0.008},
        dividend_tax_lt1m: 0.20}
```

## 11. 里程碑与任务分解

> 粒度原则（PM 评审）：一个任务 ≈ 一次 Claude Code 会话可完成、可独立验收。

### M0 · 数据可行性 Spike（最先执行，产出 go/no-go 报告）
- **T0.1** 逐行验证 §3.1 的 11 行数据源：实拉样本、记录字段/耗时/失败率/所需 Tushare 积分。AC：每行标记 ✅/降级/❌；累计积分成本 ≤ ¥500/年，超出列降级清单。
- **T0.2** daily_basic 回填验证：抽 10 只票 × 各 5 个随机历史日，`pe_ttm` 与"当日收盘价 ÷ 当期已披露 EPS_ttm"手工重算比对。AC：偏差 < 3% 的样本占比 ≥ 90%。
- **T0.3** 历史截面重建演练：重建 2022 年任一周的筛池输入并产出该周合格名单。AC：名单可产出且行业字段齐全。

### M0.5 · 工程脚手架（PM 评审新增）
- **T0.5.1** repo 骨架 + pyproject + pytest + pre-commit + .env 约定。AC：`pytest` 空跑绿；`.env.example` 存在。
- **T0.5.2** `common/config.py`：pydantic 加载 §10 全部 yaml。AC：非法配置报错定位到字段；单测覆盖缺字段/类型错/越界。
- **T0.5.3** CLAUDE.md：三条铁律、目录约定、"AC 全绿才进下一任务"、fixture 录制回放操作说明。AC：文件存在且内容与本文档一致。
- **T0.5.4** `common/calendar.py` 交易日历（Tushare trade_cal 落库+查询）。AC：2019–2026 任意日期的 is_trading_day / next / prev 单测通过。
- **T0.5.5** `tests/synth/` 合成K线生成器：可生成 uptrend/downtrend/sideways/breakout/crash/recover 六种形态（供策略方向单测用）。AC：六形态生成可复现（固定种子）。

### M1 · 垂直切片：丑版端到端（第一次每日可用）
- **T1.1** 最小 Repository（kline/valuation/signals 三表）+ WAL 单写线程。AC：并发读写压测无 lock 错误。
- **T1.2** Tushare 适配器（daily/adj_factor/daily_basic）+ 令牌桶 + fixture 录制回放。AC：回放单测通过，CI 不打真实 API。
- **T1.3** 手工名单（config 里 20 只票）→ 日K+估值最小同步（增量、as_of_date 支持）。AC：跨除权日增量更新后复权收益序列连续（单测：模拟除权事件）。
- **T1.4** indicators.py（SMA/ATR/RSI/动量，动态复权）。AC：与手算已知值对照单测。
- **T1.5** S1 策略 + 基类 + 状态机。AC：六形态合成K线方向单测（breakout 触发买入、crash/downtrend 不触发、吊灯止损正确退出）。
- **T1.6** 每日 CLI：`tally run --until today` 输出 markdown 信号报告（原因/止损/固定仓位建议）+ Slack 推送。AC：幂等（同日重复跑产物一致）；报告含"未经回测验证，仅观察"标注。
- **里程碑出口**：用户开始每日收到 S1 观察信号，反馈呈现格式与运维体感。

### M2 · 数据层完备 + 回测基建 + S1 过门
- **T2.1** 全量 DDL + Repository 扩展。AC：全表 CRUD 单测。
- **T2.2** 财务链路：fundamentals_raw/derived 同步 + derived.py（roe_ttm/rev_yoy_q）。AC：与 Tushare fina_indicator 现成字段抽样对账偏差 < 1%。
- **T2.3** 事件链路：earnings_events（forecast/express/report/预约披露）+ limit_prices + 行业。AC：抽 5 只票人工核对 2024 年事件完整性。
- **T2.4** 真实周度筛池（入池/维持分离、宽限、黑白名单）。AC：重建 2022/2024 任意周结果确定可复现。
- **T2.5** broker.py + 单策略回测 engine + metrics。AC：黄金用例（3 票 × 20 日，人肉核对每笔成交与净值）逐笔一致；防未来函数测试（截断 vs 全量，T 日信号逐字节一致）。
- **T2.6** S1 信号密度干跑（2019–2025 全池）。AC：密度落入 §5.3 区间，否则调阈值并记录。
- **T2.7** **S1 回测过门**：训练段调参 → 样本外验证（占用 1 次 OOS 预算）。AC：§5.2 S1 验收标准；不过门 → 按归因修订后重跑（再占预算需审慎）。

### M3 · S2/S4 滚动过门 + 组合决策门
- **T3.1** S2 策略实现（含事件日 R 逻辑）。AC：合成事件单测（放量跳涨触发/预告透支不触发/5 日不收复作废）。
- **T3.2** S2 回测过门。AC：样本外超额 > 0；45 日参数敏感性 {30,45,60} 同向。
- **T3.3** S4 策略实现。AC：六形态单测 + 崩溃排除单测（跌停/天量样本不触发）。
- **T3.4** S4 回测过门（军令状全网格 + 低开撮合复测）。AC：§5.2 S4 军令状；任一翻转则 S4 移除出一期（配额重归一为 S1 55%/S2 35%）。
- **T3.5** 双闸门 gates.py。AC：规则单测（含防抖期冻结、dd 熔断触发/解除、取更严者）。
- **T3.6** 组合合成回测（allocator 简版：配额+裁决器）。AC：§7 组合级验收全部出数。
- **里程碑出口 = 总决策门**：样本外达标放行 M4；不达标按归因裁剪回到对应策略任务。

### M4 · 组合宪法完备 + 日常闭环
- **T4.1** ledger.py 双账本 + executions 回填 + 账本失真防护。AC：回填后实际账本重算正确；止损锚定真实成交价；防护降级规则单测。
- **T4.2** allocator/constraints 完整版（漂移带/行业权重上限/同票合并）。AC：§4.2 五条裁决规则场景单测全覆盖。
- **T4.3** catch-up runner 完整版 + launchd 模板 + §8.5 时刻表（信号隔日作废）。AC：模拟"隔 3 天开机"补跑正确且信号作废逻辑生效。
- **T4.4** 看板三页 + CLI 回填。AC：单条信号回填 ≤3 次交互；三页可用。
- **T4.5** Slack 三类告警 + 日报终版。AC：三类告警各有触发单测。

### M5 · 试运行（时长 = max(8 周, 累计 100 条信号)，依 T2.6 干跑密度反推预估并写入计划）
双账本并行；周报执行偏离；按 §7 停用规则监控。

### §11.1 一期成功标准（M5 结束时判定，四条全过 → 启动二期；否则复盘）
1. 模型账本 buy 组 T+20 跑赢基准的比例，高于 hold 对照组 ≥ 5 个百分点；
2. 数据管道无人工干预连续运行 ≥ 4 周（sync_failures 自动消化，零手工修数）；
3. 用户日均使用 ≤ 10 分钟且回填执行率 ≥ 70%；
4. 至少 1 条策略实盘期滚动表现落入其回测样本外置信区间（±2σ）。

## 12. 风险登记册

| 风险 | 应对 |
|---|---|
| Tushare 积分不足 | T0.1 实测积分需求，预算上限 ¥500/年，超出逐项降级 |
| 回测决策门不过 | 按单规则归因裁剪不硬上；最小预案 = 仅 S1+S2（配额 55/35 + 10 现金） |
| 用户回填率低 | ≤3 次交互 AC + CLI 快捷回填 + 账本失真防护自动降级 |
| H00300/预约披露接口失效 | §3.1 降级链 + 健康检查告警 |
| 单人业余弃坑 | M1 垂直切片 4 周内见到每日产出；任务粒度一晚一个 |

---

## 附录 A · 二期规格存档（本期不实现，实现时以本附录为准）

### A.1 美股腿
数据源：yfinance 日K（显式 `auto_adjust=False`）、`fast_info`+`info` 错峰分批筛池、`^SP500TR` 基准、Wikipedia 成分（含修订历史与 GICS Sector）；财务生效统一按报告期末+45 自然日；S2 事件日用价格推断法（公告日与次日中放量跳涨者为 R）；S4 健康检查降级为"非财报窗口"；无 T+1/涨跌停，broker 按美股规则分支；独立资金池与配额（S1 40%/S2 40%/S4 20%）、独立闸门（^SP500TR，vol 阈值 0.25）。前置 Spike：美股数据源矩阵 7 行逐项验证（earnings 日期精度、info 限流失败率为重点）。

### A.2 S3 质量折价回归（仅 A股，long）
- 质量：`roe_ttm ≥ 12%` 且连续 4 季 ≥ 10%；近 4 季 ≥3 季 `ocf_q > 0`；最新营收同比 > −15%；剔除非标审计意见（Tushare fina_audit）；问询函人工黑名单。
- 估值：`pb ≤ 自身3年日频序列20%分位`（daily_basic 回填）；`pe_ttm > 0`。
- 入场：距 60 日高点回撤 > 15% 且相对基准 20 日超跌 > 8%；`close > close[-3]`（止跌）；财报新鲜度 ≤ 60 交易日；退出后 60 日冷却。
- 退出：PB 回自身 60% 分位；新财报 `roe_ttm < 8%` 或连续两季 `ocf_q < 0` 无条件清仓；硬止损 −15%；250 日时间退出。
- 闸门特例：熊市禁新开仓（一期规则），二期评估"熊末例外"（dd 触发熊市但 20 日动量转正时放行半额度）。

### A.3 S5 盈利动量持有（仅 A股，long，月末调仓，同持 ≤8 只）
- 入场：连续 3 个已披露季度 `rev_yoy_q > 10%` 且 `roe_ttm > 15%`；近 2 季 `ocf_q > 0`；`pe_ttm ≤ 自身3年70%分位`；120 日累计跑赢基准。超额按 roe_ttm 降序取 8。
- 联动：S2 持仓 45 日到期若满足上述条件 → 批次转轨长持（入场价不变）。
- 退出：新财报 `rev_yoy_q < 0` 或 `roe_ttm < 12%` 或 `ocf_q < 0` 即时退出；持有期最高收盘价回撤 −20%；`pe_ttm > 自身95%分位`。
- 验收：跌停日不可卖出撮合复测；删任一入场条件收益大变则废弃。
- 长持类闸门规则：熊市禁新开、存量按自身规则退出、不强制减仓；停用规则用"单笔亏损分布超出回测最坏 10% 分位"（非胜率口径）。

### A.4 二期其他
看板③个股详情（K线+信号标注）④成绩单图表增强 ⑥回测实验室（提交参数→CLI 子进程→展示 backtest_runs）；五策略版配额恢复为 S1 25/S2 20/S3 20/S4 10/S5 25；组合级验收扩展（五策略相关矩阵、2021 段熔断专项）。

---
*免责声明：本系统输出为规则引擎的机械结果，不构成投资建议。*
