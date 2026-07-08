# Tally（明账）· 实现规格文档（Implementation Spec）

> 项目名：**Tally**（中文名：明账）｜ repo：`tally` ｜ CLI 命令：`tally`
> 命名由来：tally stick（符木）——金融史上最早的防篡改双账本，对应本系统"模型账本 vs 真实持仓"的双账本设计；"明账" = 每条建议明明白白记账、可追溯、可证伪。
> 版本 v2.2.1 ｜ 2026-07-08 ｜ 面向 Claude Code 的工程实现文档
> 演进链：PRD v1.0（量化+架构双审）→ 策略共识（三研究员辩论）→ 金融复审（组合风控总监+数据执行专家）→ 架构重定义 → PM 评审 → v2.1（美股 S1/S2/S4 回归一期，产品负责人决策）→ v2.2（美股改动三视角复审后修订：引入 SEC XBRL 作美股财务主源、定义 MarketProfile、拆分 M3 任务、修复 12 项工程一致性问题）→ **v2.2.1（产品负责人决策：通知渠道 Slack→Telegram；美股 US.enabled 默认 false，A股稳跑 4 周后再开）**
> 本文档为唯一实现依据（single source of truth），信息自足，不依赖任何外部文档。

---

## 0. 给实现者（Claude Code）的说明

- 按 §11 里程碑顺序实现。每个任务有可判定的验收标准（AC），**AC 全绿才能进入下一任务**。
- 三条铁律（写入 CLAUDE.md，任何实现不得违反）：
  1. **防未来函数**：任何指标计算只能使用截断到 `as_of_date` 的数据；财务数据一律按其生效日（见 MarketProfile.fin_effective_rule）之后可见。
  2. **回测与实盘同源**：策略/风控/组合代码只有一份，回测引擎只负责喂"截断到 T 日"的数据切片。
  3. **所有持久化经 Repository**：业务模块禁止裸 SQL。
- 所有可调参数放 `config/*.yaml`，代码中不得出现魔法数字。
- 技术栈：Python 3.11+；pandas、numpy、pydantic、typer（CLI）、streamlit、plotly、tushare、akshare、yfinance、pandas_market_calendars（NYSE 日历）、lxml（Wikipedia 成分表解析）、requests（SEC EDGAR）、pytest。
- 密钥：`TUSHARE_TOKEN`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` 走 `.env`（入 `.gitignore`），config 中以 `env:VAR` 引用。SEC EDGAR 无需 key，但必须设置合规 `User-Agent`（含联系邮箱）。
- 数据库演进：一期允许"删库重同步"（数据均可从源重建），DDL 变更无需迁移脚本，但须同步更新本文档 §3.2。

## 1. 系统目标与一期范围

每周按基本面标准维护股票池；每日对池内股票运行策略引擎，输出可解释的买入/卖出建议（原因、止损价、仓位）；用户通过看板/CLI 回填实际成交；系统持续追踪建议表现；策略上线前必须通过历史回测决策门。

**一期范围（v2.2）**：**A股 + 美股双市场 × 三条策略（S1 趋势突破 / S2 事件PEAD / S4 恐慌回补）+ 双闸门 + 看板核心三页**。两市场为独立封闭资金池，独立基准与闸门；实现顺序为 **A股链路先行**（M1–M2），美股链路在 M3 接入（数据最脆弱，放在骨架验证之后）。

**明确移入二期**（附录 A 保留完整规格，供二期直接实现）：
- S3 质量折价、S5 盈利动量（长持策略，试运行期内积累不出可评估样本；其美股腿需 3 年估值序列，即使引入 SEC XBRL 也需先积累估值快照）
- 看板③个股详情、④成绩单详情页的图表增强、⑥回测实验室（CLI 已能跑回测）
- 问询函自动监控（降级为人工黑名单）、A+美混合净值、汇率、自动下单、ML、多用户

## 2. 总体架构

```
┌─ 入口A: tally CLI（幂等 catch-up runner，as_of_date 贯穿全链路）
├─ 入口B: dashboard（Streamlit，一期三页）
│
├─ portfolio/                组合层（一等模块）
│   ├─ ledger.py             双账本：模型账本 + 实际账本
│   ├─ allocator.py          软配额 + 现金争用裁决器 + 停用重归一
│   ├─ gates.py              MarketRegimeGate + PortfolioDrawdownGate
│   └─ constraints.py        单票/行业/同票合并上限（权重口径）
├─ strategy/
│   ├─ base.py               Strategy 基类 + 持仓状态机
│   ├─ s1_breakout.py / s2_pead.py / s4_panic_reversion.py
│   ├─ indicators.py         技术指标（动态复权）
│   └─ registry.py           策略注册、配置装配、market_overrides 深合并
├─ pool/screener.py          入池标准 与 持仓维持标准 分离（按 market 参数化）
├─ backtest/
│   ├─ engine.py             单策略回测 + 组合合成回测（同一撮合器）
│   ├─ broker.py             撮合规则按市场分支（A股 T+1/涨跌停/税费；美股 IBKR 佣金/预扣税）
│   └─ metrics.py            绩效 + 单规则归因 + 策略相关矩阵
├─ tracking/                 信号追踪（模型口径）+ 执行偏离（实际口径）
├─ data/
│   ├─ sources/              tushare_source.py（A股主源）/ akshare_source.py（A股辅源+降级）
│   │                        yfinance_source.py（美股行情主源）/ stooq_source.py（美股日K降级+退市票）
│   │                        sec_xbrl_source.py（美股财务主源）/ sp500_membership.py（成分含历史）
│   ├─ repository.py         唯一 SQL 入口
│   ├─ sync.py               全局令牌桶 / 单写线程 / sync_failures 补拉
│   └─ derived.py            派生指标：roe_ttm、rev_yoy_q（按 market 区分累计/单季口径）
│                            （估值分位为二期 S3/S5 用，一期不实现）
├─ common/                   config(pydantic) / calendar(按 market 分派) / market_profile
│                            logging / notify(telegram)
└─ tests/                    fixtures/（录制回放）+ golden/（黄金用例）+ synth/（合成K线生成器）
```

**架构决策记录（ADR）**：

| # | 决策 | 依据 |
|---|---|---|
| ADR-1 | Tushare Pro 为 A股行情+财务+估值主源；AkShare 为辅源与降级通道 | 数据复审：18 项字段缺口中 11 项唯 Tushare 稳定提供 |
| ADR-2 | 市场资金池完全独立封闭（A股/美股各一套净值、配额、闸门、基准） | 双券商+外汇管制的物理现实 |
| ADR-3 | 双账本：模型账本（假设全执行，度量策略）+ 实际账本（用户回填，驱动风控） | 建议式系统的账本漂移问题 |
| ADR-4 | 组合模型为"软配额+统一现金池"；废除早期版本的 2% 风险仓位公式与 15只/10% 单账本 | 消解组合模型矛盾 |
| ADR-5 | 出池仅禁新开仓，存量按策略自身规则退出（ST/退市/停牌超60日除外） | 防止池规则截断赢家右尾 |
| ADR-6 | PE 口径统一 PE_ttm（Tushare daily_basic），"东财动态PE"仅展示 | 动态PE无历史序列不可回测 |
| ADR-7 | 一期砍 S3/S5（长持策略），与风险预案对齐 | PM 评审：试运行期积累不出可评估样本 |
| ADR-8 | 美股 S1/S2/S4 保留在一期，但排在 A股链路之后（M3 接入） | 产品负责人决策（2026-07-08）推翻 PM 的美股延期建议；数据可行性硬约束仍遵守 |
| ADR-9 | 美股财务主源 = SEC EDGAR XBRL（companyfacts，免费、全历史、含真实 filed 披露日、含退市公司）；yfinance 季报仅作交叉校验 | v2.2 复审：yfinance 仅 4–5 季，S2 美股腿"增速环比"需 6 季且回测需全历史，yfinance 方案实盘要等一个季度、回测完全无数据 |
| ADR-10 | 美股买卖建议一律按 T+1 开盘执行撮合（与 A股同）；"无 T+1"仅指交易所无限制，本系统建议链路时序不因市场而异 | v2.2 复审：回测与实盘同源铁律；系统在收盘后才产出信号 |

## 3. 数据层规格

### 3.1 数据源矩阵（M0 逐项验证）

**A股（#1–11）**：

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
| 11 | A股交易日历 | Tushare `trade_cal` | AkShare 交易日历 | 每年 |

**美股（#12–20，M3 接入，M0 一并验证）**：

| # | 数据 | 主源（接口） | 降级 | 频率 |
|---|---|---|---|---|
| 12 | 日K | yfinance `history(auto_adjust=False)`。**注意：该口径 OHLC 已含拆股调整、未含分红调整，并非真正不复权价**；落库 `adj_factor = AdjClose/Close`；同步管道检测 `splits`/`dividends` 新事件 → 该票**全量重拉覆盖**（美股专属分支，防 Yahoo 回溯改写导致新旧段错位） | Stooq CSV（复权口径，仅临时补缺与退市票，kline.source 标记，不参与 adj_factor 自算） | 每交易日 |
| 13 | 筛池基本面（市值/PE/ROE 当前值，ROE 为 ttm 口径） | yfinance `fast_info`（市值/价格，先行预筛）+ `info`（PE/ROE，错峰分批：100 只/批、批内并发 2、请求间隔 ≥0.6s、批间隔 5 分钟、429 指数退避且当批中止；两轮失败入 sync_failures，连续 2 周失败沿用上周缓存值并标注 stale） | 缓存值 + stale 标注 | 每周 |
| 14 | 财报公告日（日期级） | yfinance `get_earnings_dates`（历史深度与脏数据率由 T0.4 实测） | SEC 8-K/10-Q `filed` 日期（#15 同源） | 每周 |
| 15 | **美股季度财务三表 + 真实披露日**（营收/净利/净资产/OCF 单季值，全历史，含退市公司） | **SEC EDGAR XBRL `companyfacts`**（免费无 key，须设合规 User-Agent；us-gaap 收入 tag 多重 fallback：`Revenues`→`RevenueFromContractWithCustomerExcludingAssessedTax`→`SalesRevenueNet`；生效日 = `filed` + 1 交易日） | yfinance 季报（仅最新 4–5 季，交叉校验；作主源时生效日 = 报告期末 + 45 自然日（Q1–Q3）/ 60 自然日（Q4），回测报告标注 fallback 覆盖比例） | 每周 |
| 16 | S&P500 成分（含历史变更 + GICS Sector） | Wikipedia 当前成分表 + 页面自带 "Selected changes" 历史变更表（**单页解析，禁止走 MediaWiki 逐版本修订**）；回测宇宙初始化用 GitHub `fja05680/sp500` 历史成分数据集，与 changes 表交叉校验 | 静态清单 | 每月 |
| 17 | S&P500 全收益基准 | yfinance `^SP500TR` | `^GSPC` + 年化 1.8% 分红修正 | 每交易日 |
| 18 | 美股交易日历（含半日市） | pandas_market_calendars（NYSE） | — | 每年 |
| 19 | 美股退市/被剔除成分日K（回测用） | Stooq（保留部分退市代码） | 缺失票从回测宇宙排除，报告首页列**被排除清单及占比**（预期 5–15%），标注"幸存者偏差仅部分修正；缺失方向对 S4 为收益高估" | 一次性回补 |
| 20 | 拆股/分红事件 | yfinance `splits`/`dividends` | — | 每交易日（触发 #12 全量重拉） |

### 3.2 存储 DDL（SQLite，WAL + busy_timeout，Repository 收口）

```sql
CREATE TABLE kline (                    -- 指数复用本表, market ∈ {CN, US, INDEX}
  code TEXT, market TEXT, date TEXT,    -- date = 交易所当地交易日 YYYY-MM-DD
  open REAL, high REAL, low REAL, close REAL,
  volume REAL, amount REAL, adj_factor REAL,   -- CN: 不复权价+复权因子; US: 见 §3.1 #12
  source TEXT DEFAULT 'primary',        -- primary / stooq（降级补缺标记）
  PRIMARY KEY (code, market, date));
CREATE INDEX idx_kline_md ON kline(market, date);

CREATE TABLE valuation (
  code TEXT, market TEXT, date TEXT,
  pe_ttm REAL, pb REAL, market_cap REAL, turnover_amt REAL,
  PRIMARY KEY (code, market, date));

CREATE TABLE limit_prices (             -- 仅 A股写入
  code TEXT, market TEXT, date TEXT, up_limit REAL, down_limit REAL,
  PRIMARY KEY (code, market, date));

CREATE TABLE fundamentals_raw (
  code TEXT, market TEXT, report_date TEXT, announce_date TEXT,
  -- CN: revenue/net_profit/ocf 为累计值(YTD); US(SEC XBRL): 为单季值。derived.py 按 market 分支差分
  revenue REAL, net_profit REAL, equity REAL, ocf REAL,
  PRIMARY KEY (code, market, report_date));

CREATE TABLE fundamentals_derived (     -- derived.py 计算后缓存
  code TEXT, market TEXT, report_date TEXT, effective_date TEXT,  -- 生效日按 MarketProfile 规则
  roe_ttm REAL, rev_yoy_q REAL, ocf_q REAL, audit_opinion TEXT,
  PRIMARY KEY (code, market, report_date));

CREATE TABLE earnings_events (          -- 预告/快报/正式披露/财报日历
  code TEXT, market TEXT, ann_date TEXT,
  event_type TEXT,                      -- forecast / express / report / scheduled(预约或日历)
  period TEXT, summary_json TEXT,
  PRIMARY KEY (code, market, ann_date, event_type));

CREATE TABLE sp500_membership (         -- 美股历史成分区间表
  code TEXT, sector TEXT, in_date TEXT, out_date TEXT,   -- out_date NULL = 现任成分
  PRIMARY KEY (code, in_date));

CREATE TABLE snapshot (                 -- 周度快照, 自建 PIT
  trade_date TEXT, code TEXT, market TEXT,
  pe_ttm REAL, pb REAL, market_cap REAL, amount_20d_avg REAL,
  is_st INTEGER, list_date TEXT, industry TEXT,   -- industry: CN=申万一级 / US=GICS Sector
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
  fill_price REAL, fill_weight_pct REAL, fill_date TEXT,
  note TEXT);                           -- note='expired' 表示过期作废（区分主动放弃）

CREATE TABLE capital_flows (            -- 出入金/跨池调拨流水（系统只记录不建议）
  date TEXT, market TEXT, amount REAL, note TEXT);

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

全局令牌桶（Tushare 按积分配额、AkShare 并发 1–2、yfinance 并发 2、SEC EDGAR ≤8 req/s 且设 User-Agent）；多线程拉取单线程批量写；增量按缺失区间（美股日K例外：检测到新 split/dividend 事件的票全量重拉，见 §3.1 #12）；失败入 `sync_failures` 下次优先补拉，连续 3 天失败 Telegram 告警；K线覆盖率缺最近 3 日的股票当日跳过信号并在报告标注；每任务记录行数/时长基线，偏离 3 倍告警。

## 4. 组合层规格（资金宪法）

### 4.1 资金模型（双市场三策略版）

- A股池与美股池**完全独立封闭**（各自净值、闸门、熔断、基准）；仓位百分比分母一律为**本市场子组合当前净值**；跨池调拨为外生用户行为，系统只记录（`capital_flows` 表 + `tally capital` 命令），nav 计算按流水做时间加权基数调整。
- 软配额 + 统一现金池（每市场内独立归一）：

| 市场 | S1 趋势突破 | S2 事件PEAD | S4 恐慌回补 | 现金底仓 |
|---|---|---|---|---|
| A股目标配额 | 40% | 30% | 20% | 10% |
| 美股目标配额 | 40% | 30% | 20% | 10% |
| 漂移带 | ±10pp | ±10pp | ±5pp | — |

- **策略腿停用重归一**：任一市场任一策略腿停用（未过门/军令状翻转/试运行停用）时，该市场剩余策略按原配额等比重归一至 90%（现金底仓 10% 不变），结果四舍五入至整数 pp 后**写入 config**（非运行时计算）。例：停 S4 → S1 57%/S2 33%；停 S2 → S1 67%/S4 23%。
- 闲置额度自动回归现金池，可被其他策略在其配额上限（目标+漂移带）内借用；现金按 `cash_yield` 计息（默认 A股 1.8%/年、美股 4.0%/年）。
- **总仓上限为最高优先约束**：双闸门给出上限后，各策略配额按比例缩放。
- 单票上限 8%（S4 为 5%）；同票多策略持有合并计仓 ≤ 8%；单行业持仓权重 ≤ 25%（A股按申万一级、美股按 GICS Sector；与池的数量口径无关）。

### 4.2 现金争用裁决器（allocator.py）

同日多信号资金不足时：① 退出信号无条件优先；② 买入按"策略配额剩余空间大者优先，同级按策略回测单位风险收益排序"；③ 排不进的信号放弃不排队；④ 禁止为新信号平掉其他策略存量；⑤ 同票同日先卖后买禁止。**同一套裁决器用于回测撮合与实盘建议。**

### 4.3 双闸门（gates.py）

**MarketRegimeGate**（完整规则；**每市场独立判态**——A股用 H00300、美股用 ^SP500TR）：

```
输入: 本市场基准指数日K, 每日收盘计算, T+1 生效
trend = index_close > SMA(index_close, 200)
mom   = index_close / index_close[t-60] − 1
vol   = std(index_daily_return, 20) × √252
dd_ix = index_close / max(index_close, 近250日) − 1

熊市   = (NOT trend AND mom < −0.05) OR dd_ix < −0.20    # dd 是独立熔断条款
牛市   = trend AND mom ≥ 0.03 AND vol ≤ vol_bull_max     # A股 0.30 / 美股 0.25, 写死禁改
震荡市 = 其余
防抖: 状态切换需连续 5 个交易日满足新状态才正式切换;
     防抖确认期间(疑似切换期)冻结全部新开仓
输出总仓上限: 牛 80% / 震荡 60% / 熊 25%(仅短持类;一期三策略均为短持类)
熊市附加: S4 时间止损 7日→3日; 降档超限时按浮亏从大到小、非跌停日(A股)限价减仓
```

**PortfolioDrawdownGate**（基于**实际账本**净值，分市场独立）：`dd ≤ −10%` → 新开仓额度减半；`dd ≤ −15%` → 禁止一切新开仓；恢复至 `−8%` 以内解除。与 RegimeGate 取更严者。

**账本失真防护**：存在超过 3 个交易日未确认（status=skipped 且未过期）的信号时，双闸门与止损锚定自动降级为**模型账本口径**，日报醒目提示"实际账本不可信，请回填"。

### 4.4 双账本（ledger.py）

- 模型账本：假设每条建议按 T+1 开盘全额执行。**T+1 = 信号所属市场日历的次一交易时段**（美股信号即北京时间当晚开盘）。用途：信号追踪、策略评估、与回测对齐。
- 实际账本：以 `executions` 为准，默认 skipped。用途：持仓卖出信号对象判定、止损价锚定（真实成交价）、双闸门、集中度约束。
- 偏离度指标：建议执行率、平均执行滑点；连续 10 个交易日执行率 < 50% → Telegram 提示"建议与实际操作严重脱节"。

## 5. 策略层规格

### 5.1 Strategy 基类与状态机

```python
class Strategy(ABC):
    id: str                    # s1 / s2 / s4
    markets: list[str]         # 一期均为 ["CN", "US"]
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

### 5.1.1 MarketProfile（市场差异注入机制）

位置 `common/market_profile.py`，运行时只读（frozen dataclass），由 `strategy/registry.py` 装配策略时按市场构建，挂到 `MarketContext.profile`：

```python
@dataclass(frozen=True)
class MarketProfile:
    market: str                    # "CN" / "US"
    currency: str                  # CNY / USD
    benchmark: str                 # H00300 / ^SP500TR（含降级链，来自 system.yaml）
    calendar_name: str             # "SSE" / "NYSE"
    industry_taxonomy: str         # "SW_L1" / "GICS_SECTOR"
    has_price_limit: bool          # CN True / US False：limit_prices 查询与涨跌停类条款开关
    t_plus_one: bool               # CN True / US False：仅影响 broker 的"当日买当日卖"约束；
                                   #   建议执行时点两市场统一为 T+1 开盘（ADR-10）
    fin_effective_rule: str        # "announce_plus_1td"(CN) / "filed_plus_1td"(US主) /
                                   #   "report_end_plus_45_60cd"(US fallback)
    has_scheduled_disclosure: bool # 预约披露/财报日历可得性（S2 披露前退出、S4 回避的分支开关）
    event_date_mode: str           # "announced"(CN) / "price_inferred"(US)：S2 事件日 R 的判定方式
```

装配规则：市场级字段来自 `system.yaml` + 代码内市场常量表；**策略级参数差异一律走 `strategies.yaml` 各策略的 `market_overrides.<MARKET>` 键，语义 = 对该策略 A股基准参数块的深合并覆盖（键为 null = 删除该条款）**。策略拿到的 `self.params` 已是合并终值，主逻辑只读 `self.params` 与 `ctx.profile` 的能力开关，**禁止出现 `if market ==` 字面判断**（单测以 AST/grep 检查 `strategy/` 目录，`market_profile.py` 与 `broker.py` 除外）。

### 5.2 三条策略精确规格（参数落 `config/strategies.yaml`）

以下规则以 A股为基准表述；美股差异在每条策略末尾的「美股适配」注明，实现经 MarketProfile 与 market_overrides 表达。

**S1 趋势突破**（short）
- 入场（T 日收盘全部满足，T+1 开盘执行）：
  - `close ≥ max(high[-250:-1])`（250日新高突破）
  - `volume ≥ 1.5 × mean(volume[-20:-1])`（放量确认）
  - `close > SMA50`
  - 泡沫刹车：池内 20 日新高股票占比 > 40% 时暂停新开仓
  - 剔除当日 `close ≥ up_limit × 0.99` 的股票（涨停/近涨停，用 limit_prices 精确判定）
  - 并发排序：多信号同日按 12-1 动量分 `close[-21]/close[-252] − 1` 降序
- 退出：吊灯止损 `close < max(high[entry..T-1]) − 3×ATR20`（水位截至 T−1，防未来函数）；连续 3 日 `close < SMA50`；硬止损 −8%。
- 上线验收：样本外区间按"次日开盘价成交 + 分档滑点"回测，年化超额（对本市场全收益基准）> 0；量能倍数 {1.3,1.5,1.8}、ATR 倍数 {2.5,3,3.5}、硬止损 {−6%,−8%,−10%} 敏感性网格内期望同向。
- 美股适配：无涨跌停 → 涨停剔除条款替换为"剔除当日涨幅 > 10% 的股票"（尾部防护，对 S&P500 成分预期极少触发；阈值纳入敏感性网格 {8%,10%,15%}）；验收对 ^SP500TR 独立过门。

**S2 事件PEAD**（short）
- 事件日 R：`earnings_events` 中该股最新事件（forecast/express/report），若事件日为非交易日或盘后则顺延次一交易日。同一 period 多次事件（预告→正式）各自独立判定，先触发先用。
- 基本面腿（按 MarketProfile.fin_effective_rule 生效）：`rev_yoy_q > prev_rev_yoy_q` 且 `rev_yoy_q > 0`（**单季口径**）；`roe_ttm ≥ 8%`；`ocf_q > 0`。
- 价格腿：R 日涨幅 ≥ +3% 且 `volume[R] ≥ 2.0 × mean(volume[R-20:R-1])`；R 后 5 个交易日内 `close ≥ high[R]` 触发买入；`close > SMA200`。超 R+5 作废不追。
- 退出：45 交易日时间退出（敏感性 {30,45,60}）；新财报证伪（`rev_yoy_q < 0` 或 `ocf_q < 0`）→ 生效日清仓；硬止损 −12%；下一已知披露日前 1 交易日浮盈 < 5% 退出。
- 美股适配：事件日 R 用**价格推断法**——在 [公告日, 公告日+1] 两个交易日中取满足"涨幅≥3% 且量≥2×"的那天为 R；**平局规则：取先满足者，两日均满足取公告日当日**。基本面腿数据来自 SEC XBRL（单季值直取，无需差分；生效日 = filed+1 交易日，历史与回测同源可算——ADR-9）；"下次披露日"用 yfinance earnings 日历，取不到时该退出条款对该票停用。回测期（2019–2023）基本面腿以 SEC XBRL 历史数据全腿参与；SEC 源验证失败（T0.4）时 fallback：回测期基本面腿视为恒通过（仅验证价格腿），报告首页标注。

**S4 恐慌错杀回补**（short，单票 5%）
- 入场（全部满足）：
  - 活人判定：`close > SMA200` 且 `SMA200[T] > SMA200[T-20]`
  - 挨刀判定：`RSI3 < 15`；`close < SMA5`；`close/close[-5] − 1 < −6%`
  - 崩溃排除：近 5 日无跌停收盘（`close ≤ down_limit×1.01`）且当日非跌停收盘；`mean(amount[-5:]) / mean(amount[-60:]) < 3.0`
  - 健康检查：最新已生效 `roe_ttm > 0` 且 `ocf_q > 0`
  - 事件回避：距下一已知披露日 > 5 交易日（预约披露不可得时：1/4/7/10 月内回避半径扩大为 10 交易日）
- 退出（优先级顺序）：`RSI3 > 60`；时间止损 7 交易日（熊市 regime 减为 3）；硬止损 −7%；`close < SMA200`。跌停无法成交时次日续挂。
- 军令状（上线验收）：RSI3 阈值 {10,12,15,18,20} × 累跌 {−5%,−6%,−8%} 全网格期望同向且为正，任一翻转**整条废弃**（按市场独立执行——单市场翻转仅停该市场腿）；追加"次日低开 >5% 按开盘价止损"的现实撮合复测。
- 美股适配：
  - **alpha 假设声明（与 A股不同源，记录在案）**：A股腿赚"T+1+涨跌停延缓价格修复"的结构性钱；美股无此微观结构，美股腿 alpha 改为"大盘股流动性冲击后的短期均值回归"，该效应随高频做市竞争历史性衰减，**先验强度弱于 A股腿**。据此美股腿过门标准不放松：军令状全网格同向为正 + 费后对 ^SP500TR 样本外超额 > 0，任一不满足仅停美股腿。
  - 崩溃排除替换为：近 5 日无"单日跌幅 ≥ 10%"收盘日，且当日跌幅 < 10%（阈值纳入军令状网格 {−10%,−12%,−15%} 独立验证）；天量排除条款不变。
  - 健康检查主源 SEC XBRL（roe_ttm/ocf_q 可算）；叠加"非财报窗口"回避（公告日前后 5 交易日不入场）。earnings 日历取不到时：美股财报月（1–2 / 4–5 / 7–8 / 10–11 月中财报密集期）回避半径扩大为 10 交易日。注：财报窗口回避防不住 8-K/诉讼类非财报暴跌，此残余风险接受并由 5% 仓位上限定价。
  - 执行时点与 A股相同：退出信号按 T+1 开盘执行（ADR-10）。

### 5.3 预期信号密度（干跑校准基线，A股 T2.6 / 美股 T3.7b 验证）

| 策略 | 预期密度（A股 50 只池） | 过严/过松预警线 |
|---|---|---|
| S1 | 趋势市 0.2–0.8 条/日，震荡市近 0 | 连续 60 交易日 0 条 或 >3 条/日 |
| S2 | 财报季 0.3–1 条/日，非财报季近 0 | 财报季整季 <3 条 或 >5 条/日 |
| S4 | 震荡市 0.2–0.6 条/日 | 连续 60 交易日 0 条 或 >4 条/日 |

美股池 30 只，预期密度按 0.6 倍折算，预警线同比例调整。

## 6. 股票池层规格

- **A股入池标准**（每周一）：市值 ≥100亿、`0 < pe_ttm ≤ 40`、`0 < pb < 8`、20日日均成交额 ≥1亿（全市场日频行情算得）、上市满 1 年、非 ST。满足者按 20 日日均成交额降序，单申万一级行业 ≤ 池内数量 20%，取前 50。
- **美股入池标准**（每周一，候选宇宙 = S&P500 当期成分）：市值 ≥ $10B、`0 < PE ≤ 40`、ROE ≥ 12%、日均成交量 ≥ 100 万股。满足者按日均成交额降序，单 GICS Sector ≤ 池内数量 20%，取前 30。**权益 ≤ 0 或 ROE 字段缺失（回购型公司常见）时 ROE 条件跳过并标注，不直接淘汰。**
- **持仓维持标准**（与入池分离）：仅 ST/退市/停牌 >60 日强平；出池股票继续同步至所有策略退出。
- 出池宽限 2 周（连续 2 次周检不满足才出）；白名单永不出池 / 黑名单永不入池（人工维护，问询函走黑名单）。

## 7. 回测层规格

- **单策略回测**：该策略独占 100% 配额，输出绩效 + 单规则归因 + 参数敏感性。
- **组合合成回测**：完整 §4 组合宪法（同一份代码），分市场独立。
- **broker.py（A股规则）**：T+1（当日买不可当日卖）；停牌禁成交持仓冻结；开盘即封板（一字板）不可成交；跌停日卖单顺延次日；滑点分档 S1/S4 0.8%、S2 0.2%；佣金双边 0.05%（最低 5 元）；印花税卖出侧分段（2023-08-28 前 0.1%，之后 0.05%）；持股 <1 月红利税 20%（撞除息日时）。
- **broker.py（美股规则分支）**：无交易所 T+1/涨跌停约束，但**撮合时点与 A股一致 = 信号次日开盘价**（ADR-10）；滑点分档 S1/S4 0.5%、S2 0.2%（**主口径**；另跑 10bp 现实口径对照——仅在主口径下不过门的策略，归因记录为"成本敏感"而非"信号失效"，停用决策仍以主口径为准）；佣金 IBKR 阶梯（$0.005/股，最低 $1）；红利预扣 10%；境外资本利得税 20% 可选参数（默认开启，输出税前/税后双口径）。
- **回测数据前提（美股）**：
  - 候选宇宙 = `sp500_membership` 历史成分重建当期 S&P500（防前视加入）；已退市/被剔除成员日K 主源 Stooq，仍缺失的票从宇宙排除，报告首页列被排除清单及占比并标注偏差方向（见 §3.1 #19）。
  - 回测池规则 = 当期历史成分 ∩ 市值近似（价格×最新股本或成交额代理）∩ 20日日均成交额降序取 30；**PE/ROE 条件仅实盘筛池生效**（无历史截面），报告首页标注"美股回测池与实盘池规则存在差异"。若 SEC XBRL 验证通过，PE/ROE 可部分回补，此差异收窄。
  - 财务生效按 SEC `filed`+1 交易日（主）；fallback 假设（期末+45/60 日）覆盖比例在报告标注。
- **防过拟合**：训练段 2019–2023 / 样本外 2024 至今；样本外查看预算 3 次（backtest_runs.is_oos 强制计数，超支需 `--exceed-oos-budget` 且报告标注）；每 run 记 git commit。
- **OOS regime 分层（美股专项）**：美股 OOS 报告须按 regime 分层输出，强制标注"2024+ 样本外无熊市样本，S1/S4 的 OOS 指标代表牛市环境，熊市条款未经样本外验证"；训练段内 2020-02～04、2022 全年作为强制压力段单独出数（与"美股 2022 段专项"合并执行）。
- **组合级验收**（每市场独立执行）：组合样本外最大回撤 < 最深单策略回撤；组合月度收益与任一单策略相关 < 0.9；三策略日收益两两相关 < 0.7（超过视为伪分散触发降配）；熊市段专项报告（A股 2018 段、美股 2022 段；专项报告允许使用 train 窗口前数据，仅用于闸门有效性评估，不用于调参）；DD 闸门有效性——熊市段熔断版回撤 ≤ 无熔断版 75%。
- **策略停用规则**（试运行期）：滚动 30 信号胜率 < 回测样本外胜率 − 2σ → 停用并复盘（按策略腿 = 策略×市场独立执行）。

## 8. 追踪、报告与日常操作

### 8.1 信号追踪（模型账本口径）
T+1/T+5/T+20 复权收益 vs 基准（A股对 H00300、美股对 ^SP500TR，T+N 按各自市场交易日历推算）；主口径"T+20 跑赢本市场基准"；buy 与 avoid 分开统计；hold（观望）为对照组。

### 8.2 执行偏离（实际账本口径）
执行率、执行滑点，周报输出；"过期作废（expired）"与"主动放弃"分开统计。

### 8.3 日报内容（Telegram 摘要 + 看板详情，**分市场成段**）
今日信号（按市场→策略分组：股票/原因列表/止损价/建议仓位/建议限价）、待回填提醒、regime 状态与总仓上限（分市场）、组合 dd（双账本、分市场）、数据健康摘要。

### 8.4 告警（三类）
任务最终失败；数据源连续异常/基线偏离；执行率过低或账本降级。

### 8.5 日常操作时刻表

| 时点（北京时间） | 动作 | 主体 |
|---|---|---|
| A股交易日 16:30 | launchd 触发 `tally run --market CN`：同步→筛池(周一)→信号→追踪→报告→Telegram | 系统 |
| 当晚任意时间 | 阅读 A股 Telegram 摘要（≤3 分钟）；回填昨日成交（看板或 CLI） | 用户 |
| 次日 9:15–9:25 | 按建议在 A股券商 App 挂集合竞价单（日报附建议限价） | 用户 |
| 美股交易日次日 07:00 | launchd 触发 `tally run --market US`（美股收盘后）：同链路，日报与 A股合并展示、分市场成段 | 系统 |
| 当天白天 | 阅读美股摘要；当晚 21:30/22:30 美股开盘时执行并回填 | 用户 |
| 电脑未开机 | launchd 错过后开机自动补触发；信号有效期 1 个交易日（按信号所属市场日历），过期未执行自动标记 expired（语义 = skipped） | 系统 |

## 9. 看板（Streamlit，一期三页）

- **页① 今日信号与回填**：信号卡片带**市场徽标与币种**；filled/partial/skipped 三态回填（写 executions，回填即覆盖）；过期信号灰显不可回填。AC：单条信号回填 ≤3 次交互。CLI 快捷通道：`tally fill <signal_id> --price 12.3 --pct 5`。
- **页② 组合总览**：顶部 CN/US 市场切换（Tab），每市场独立展示双账本净值、dd、regime 历史、策略配额占用、行业集中度（申万一级 / GICS 各自口径）、同票重叠；**不提供合并净值**（二期）。
- **页⑤ 策略成绩单**：全部指标按 **市场 × 策略** 分组，滚动胜率（跑赢本市场基准口径）、T+5/T+20 平均超额、信号组 vs 观望组、执行偏离趋势。

## 10. 配置文件规格

```yaml
# config/system.yaml
markets:
  CN:
    enabled: true
    initial_capital: 500000        # CNY
    benchmark: H00300              # 降级链: H00300 -> 510300_adj -> 000300+2.2%
    cash_yield: 0.018
  US:
    enabled: false                 # 灰度:A股链路满足成功标准2(无人工干预连续跑≥4周)后再置 true
    initial_capital: 50000         # USD
    benchmark: ^SP500TR            # 降级链: ^SP500TR -> ^GSPC+1.8%
    cash_yield: 0.040
tushare_token: env:TUSHARE_TOKEN
telegram_bot_token: env:TELEGRAM_BOT_TOKEN
telegram_chat_id: env:TELEGRAM_CHAT_ID
sec_user_agent: "Tally/1.0 (qingzhu.liu@cobo.com)"   # SEC EDGAR 合规要求
rate_limits: {tushare_per_min: 400, akshare_concurrency: 2, akshare_interval_s: 0.5,
              yfinance_concurrency: 2, sec_edgar_rps: 8}

# config/portfolio.yaml —— 每市场一套, 结构相同
CN:
  quotas: {s1: 0.40, s2: 0.30, s4: 0.20}      # 现金底仓 0.10；策略腿停用时按 §4.1 重归一后改写本文件
  drift_band: {s1: 0.10, s2: 0.10, s4: 0.05}
  per_stock_cap: {default: 0.08, s4: 0.05}
  industry_cap_weight: 0.25                    # 申万一级
  regime: {bull_exposure: 0.80, range_exposure: 0.60, bear_exposure: 0.25,
           sma_n: 200, mom_n: 60, mom_bear: -0.05, mom_bull: 0.03,
           vol_n: 20, vol_bull_max: 0.30, dd_bear: -0.20, confirm_days: 5}
  dd_gate: {half: -0.10, freeze: -0.15, release: -0.08}
US:
  quotas: {s1: 0.40, s2: 0.30, s4: 0.20}
  drift_band: {s1: 0.10, s2: 0.10, s4: 0.05}
  per_stock_cap: {default: 0.08, s4: 0.05}
  industry_cap_weight: 0.25                    # GICS Sector
  regime: {bull_exposure: 0.80, range_exposure: 0.60, bear_exposure: 0.25,
           sma_n: 200, mom_n: 60, mom_bear: -0.05, mom_bull: 0.03,
           vol_n: 20, vol_bull_max: 0.25, dd_bear: -0.20, confirm_days: 5}
  dd_gate: {half: -0.10, freeze: -0.15, release: -0.08}
ledger_guard: {unconfirmed_days: 3, exec_rate_alert: 0.50, exec_rate_window: 10}

# config/strategies.yaml —— S1 为完整范式; 美股差异一律走 market_overrides.US（深合并, null=删除条款）
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
  market_overrides:
    US: {entry: {limit_up_exclude_ratio: null, daily_gain_exclude: 0.10}}
s2_pead:
  enabled: true
  entry: {r_jump_min: 0.03, r_vol_mult: 2.0, confirm_window: 5,
          roe_ttm_min: 0.08, ma_gate: 200}
  exit: {time_days: 45, hard_stop: -0.12, pre_report_min_profit: 0.05}
  market_overrides:
    US: {entry: {r_mode: price_inferred_2d}}    # 事件日推断与生效规则走 MarketProfile
s4_panic_reversion:
  enabled: true
  entry: {rsi_n: 3, rsi_oversold: 15, drop_5d: -0.06, ma_trend: 200,
          ma_trend_rising_n: 20, ma_short: 5, vol_spike_max: 3.0,
          event_avoid_days: 5, event_avoid_days_fallback: 10}
  exit: {rsi_exit: 60, time_days: 7, time_days_bear: 3, hard_stop: -0.07}
  market_overrides:
    US: {entry: {crash_day_drop: -0.10}}        # 等效崩溃日阈值, 军令状网格 {-0.10,-0.12,-0.15}

# config/pool.yaml
CN:
  entry: {min_mcap: 1.0e10, pe_ttm_range: [0, 40], pb_range: [0, 8],
          min_amount_20d: 1.0e8, min_list_days: 250, exclude_st: true}
  size: 50
  industry_cap_count: 0.20
  exit_grace_weeks: 2
US:
  universe: sp500
  entry: {min_mcap_usd: 1.0e10, pe_range: [0, 40], min_roe: 0.12, min_avg_volume: 1.0e6,
          roe_skip_if_missing: true}
  size: 30
  industry_cap_count: 0.20
  exit_grace_weeks: 2

# config/backtest.yaml
train: {start: "2019-01-01", end: "2023-12-31"}
oos: {start: "2024-01-01", view_budget: 3}
costs:
  CN:
    commission: 0.0005
    commission_min: 5              # CNY
    stamp_duty: [{until: "2023-08-27", rate: 0.001}, {from: "2023-08-28", rate: 0.0005}]
    slippage: {s1: 0.008, s2: 0.002, s4: 0.008}
    dividend_tax_lt1m: 0.20
  US:
    commission_per_share: 0.005    # USD, IBKR 阶梯
    commission_min: 1              # USD
    slippage: {s1: 0.005, s2: 0.002, s4: 0.005}    # 主口径; 另跑 0.001 现实口径对照
    slippage_realistic: 0.001
    dividend_withholding: 0.10
    capital_gains_tax: {enabled: true, rate: 0.20}
```

## 11. 里程碑与任务分解

> 粒度原则（PM 评审）：一个任务 ≈ 一次 Claude Code 会话可完成、可独立验收。

### M0 · 数据可行性 Spike（最先执行，产出 go/no-go 报告）
- **T0.1** 逐行验证 §3.1 的 A股 11 行数据源：实拉样本、记录字段/耗时/失败率/所需 Tushare 积分。AC：每行标记 ✅/降级/❌；累计积分成本 ≤ ¥500/年，超出列降级清单。
- **T0.2** daily_basic 回填验证：抽 10 只票 × 各 5 个随机历史日，`pe_ttm` 与"当日收盘价 ÷ 当期已披露 EPS_ttm"手工重算比对。AC：偏差 < 3% 的样本占比 ≥ 90%。
- **T0.3** 历史截面重建演练（A股）：重建 2022 年任一周的筛池输入并产出该周合格名单。AC：名单可产出且行业字段齐全。
- **T0.4** 逐行验证 §3.1 的美股 9 行数据源。重点实测：① **SEC XBRL companyfacts**——抽 5 只票核对 2019 起逐季营收/OCF 与 filed 日期齐全性，收入 tag fallback 链有效性（ADR-9 的 go/no-go）；② `info` 限流——50 只 × 并发 {1,2,4} 三档失败率/耗时 + 全量 500 只一轮分批策略验证；③ `get_earnings_dates` 历史深度与脏数据率（抽 10 只对照 2019–2023 实际公告日，覆盖率 <90% 时 S4 回避降级为财报月模式）；④ 复权自算——AAPL 2020-08-31、TSLA 2022-08-25 拆股日前后自算复权收益 vs `auto_adjust=True` 逐日比对偏差 <0.1%；⑤ 退市票可得率——从 changes 表抽 2019 以来被移出的 20 只，测 yfinance/Stooq 价格历史可得率（≥80% 通过，不足则按 #19 降级）；⑥ Wikipedia changes 表解析 + fja05680 数据集 diff（差异 <5 条/年）。AC：每行标记 ✅/降级/❌；SEC XBRL ❌ 时触发 S2 美股腿 fallback 决策（§5.2）并更新 ADR-9。

### M0.5 · 工程脚手架
- **T0.5.1** repo 骨架 + pyproject + pytest + pre-commit + .env 约定。AC：`pytest` 空跑绿；`.env.example` 存在。
- **T0.5.2** `common/config.py`：pydantic 加载 §10 全部 yaml（含 market_overrides 深合并逻辑）。AC：非法配置报错定位到字段；深合并与 null 删除语义单测。
- **T0.5.3** CLAUDE.md：三条铁律、目录约定、"AC 全绿才进下一任务"、fixture 录制回放操作说明。AC：文件存在且内容与本文档一致。
- **T0.5.4** `common/calendar.py` 交易日历，**接口签名含 market 参数**（一期实现 CN=SSE；NYSE 在 T3.6a 注册进同一接口）。AC：2019–2026 任意日期的 is_trading_day/next/prev 单测；接口按 market 分派。
- **T0.5.5** `tests/synth/` 合成K线生成器：六种形态（uptrend/downtrend/sideways/breakout/crash/recover），**支持参数化：单日极端涨跌幅注入、是否施加涨跌停约束（美股模式）、伴随事件日标注序列**。AC：全形态固定种子可复现。

### M1 · 垂直切片：丑版端到端（第一次每日可用，A股）
- **T1.1** 最小 Repository（kline/valuation/signals 三表）+ WAL 单写线程。AC：并发读写压测无 lock 错误。
- **T1.2** Tushare 适配器（daily/adj_factor/daily_basic）+ 令牌桶 + fixture 录制回放。AC：回放单测通过，CI 不打真实 API。
- **T1.3** 手工名单（config 里 20 只票）→ 日K+估值最小同步（增量、as_of_date 支持）。AC：跨除权日增量更新后复权收益序列连续（单测：模拟除权事件）。
- **T1.4** indicators.py（SMA/ATR/RSI/动量，动态复权）。AC：与手算已知值对照单测。
- **T1.5** S1 策略 + 基类 + 状态机。AC：六形态合成K线方向单测（breakout 触发买入、crash/downtrend 不触发、吊灯止损正确退出）。
- **T1.6** 每日 CLI：`tally run --market CN --until today` 输出 markdown 信号报告（原因/止损/固定仓位建议）+ Telegram 推送。AC：幂等（同日重复跑产物一致）；报告含"未经回测验证，仅观察"标注。
- **里程碑出口**：用户开始每日收到 S1 观察信号，反馈呈现格式与运维体感。

### M2 · 数据层完备 + 回测基建 + S1 过门（A股）
> 设计备注：本里程碑各组件一律按 market 参数化设计（derived 市场无关计算、筛池框架带 market、broker 以 MarketProfile/规则集参数化），仅实现 CN 分支——M3 美股接入应为"新增配置与子类"，不允许重写。
- **T2.1** 全量 DDL + Repository 扩展。AC：全表 CRUD 单测。
- **T2.2** 财务链路：fundamentals_raw/derived 同步 + derived.py（roe_ttm/rev_yoy_q，按 market 区分累计/单季口径的差分分支）。AC：与 Tushare fina_indicator 现成字段抽样对账偏差 < 1%。
- **T2.3** 事件链路：earnings_events（forecast/express/report/预约披露）+ limit_prices + 行业。AC：抽 5 只票人工核对 2024 年事件完整性。
- **T2.4** 真实周度筛池（入池/维持分离、宽限、黑白名单；框架带 market 参数）。AC：重建 2022/2024 任意周结果确定可复现。
- **T2.5** broker.py + 单策略回测 engine + metrics。AC：黄金用例（3 票 × 20 日，人肉核对每笔成交与净值）逐笔一致；防未来函数测试（截断 vs 全量，T 日信号逐字节一致）。
- **T2.6** S1 信号密度干跑（2019–2025 A股全池）。AC：密度落入 §5.3 区间，否则调阈值并记录。
- **T2.7** **S1 回测过门（A股）**：训练段调参 → 样本外验证（占用 1 次 OOS 预算）。AC：§5.2 S1 验收标准；不过门 → 按归因修订后重跑（再占预算需审慎）。

### M3 · S2/S4 滚动过门（A股）+ 美股接入 + 组合决策门
- **T3.1** S2 策略实现（含事件日 R 逻辑）。AC：合成事件单测（放量跳涨触发/预告透支不触发/5 日不收复作废）。
- **T3.2** S2 回测过门（A股）。AC：样本外超额 > 0；45 日参数敏感性 {30,45,60} 同向。
- **T3.3** S4 策略实现。AC：六形态单测 + 崩溃排除单测（跌停/天量样本不触发）。
- **T3.4** S4 回测过门（A股，军令状全网格 + 低开撮合复测）。AC：§5.2 S4 军令状；翻转则停 A股腿并触发 §4.1 重归一。
- **T3.5** 双闸门 gates.py（多市场参数化）。AC：规则单测（防抖期冻结、dd 熔断触发/解除、取更严者、双市场独立判态）。
- **T3.6a** 美股行情与日历：yfinance 日K 适配器（§3.1 #12 复权与全量重拉规则）+ Stooq 降级 + NYSE 日历注册 + fixture 录制回放 + 2019 至今池内历史回补。AC：回放单测 CI 不打真实 API；跨拆股/分红日复权收益连续（T1.3 的美股对偶单测）；回补覆盖率与耗时基线记录。
- **T3.6b** 成分、基准与筛池：sp500_membership（changes 表 + fja05680 初始化 + 静态降级）+ ^SP500TR（^GSPC+1.8% 降级）+ fast_info/info 错峰采集 + 美股周度筛池。AC：当期池可产出且 GICS 齐全；抽 2022 年任一月历史成分与已知变更记录一致；info 全量一轮失败率/耗时记录。
- **T3.6c** 美股财务与事件链路：SEC XBRL 适配器（companyfacts → fundamentals_raw/derived，生效日 = filed+1 交易日）+ get_earnings_dates → earnings_events + yfinance 季报交叉校验。AC：抽 5 只票季报字段与 SEC 原始 filing 核对；生效日防未来函数单测（生效日前查询不可见）；与 yfinance 最新季度交叉偏差 < 2%。
- **T3.7a** MarketProfile 机制（§5.1.1）+ broker 美股分支。AC：美股黄金用例（3 票 × 20 日：次日开盘撮合、无涨跌停、IBKR 佣金含最低 $1、预扣 10%、税前/税后双口径）逐笔一致；`if market ==` 禁令 AST 检查单测通过。
- **T3.7b** 三策略美股腿实现（market_overrides 生效）+ 美股信号密度干跑（2019–2025）。AC：§5.2 各"美股适配"条款单测（10% 涨幅剔除、−10% 等效崩溃日、价格推断 R 含平局规则、非财报窗口回避）；密度落入 §5.3 折算区间。
- **T3.7c** 美股回测过门：S1/S2/S4 各自独立对 ^SP500TR 过门（费后主口径 + 10bp 对照；S4 军令状按市场独立；S2 全腿参与——SEC 数据；OOS regime 分层标注）。AC：不过门策略仅停美股腿并触发 §4.1 重归一，不影响 A股。
- **T3.8** 组合合成回测（allocator 简版：配额+裁决器，双市场独立）。AC：§7 组合级验收全部出数（每市场一套）。
- **里程碑出口 = 总决策门**：A股组合样本外达标即放行 M4（美股腿允许带"部分策略停用"状态进入 M4）；A股不达标按归因裁剪回到对应策略任务。

### M4 · 组合宪法完备 + 日常闭环
- **T4.1** ledger.py 双账本 + executions 回填 + capital_flows 流水与 `tally capital` 命令 + 账本失真防护。AC：回填后实际账本重算正确；止损锚定真实成交价；流水后 nav 时间加权正确；防护降级规则单测。
- **T4.2** allocator/constraints 完整版（漂移带/行业权重上限/同票合并/停用重归一）。AC：§4.2 五条裁决规则场景单测全覆盖。
- **T4.3** catch-up runner 完整版 + launchd 双计划 + 信号过期。交付：① `tally run --market {CN,US} [--until DATE]`，每市场独立 catch-up 游标；② 两份 launchd plist（tally-cn 16:30 / tally-us 07:00，均含错过后开机补触发）；③ 过期规则：信号于其所属市场交易日 D 生成，预期执行日 = 该市场日历 D 的次一交易日，runner 推进过执行日仍未回填 → note='expired'。AC：模拟"隔 3 天开机"CN/US 各自补跑正确；**含中美节假日错位周场景（如国庆周 CN 休市 US 开市）**两游标互不污染；过期标记单测。
- **T4.4** 看板三页 + CLI 回填。AC：单条信号回填 ≤3 次交互；CN/US 切换数据正确隔离；美股信号回填链路与 A股一致。
- **T4.5** Telegram 三类告警 + 日报终版（分市场成段）。AC：三类告警各有触发单测。

### M5 · 试运行（时长 = max(8 周, **A股累计 100 条信号**)，美股信号单独计数不并入门槛）
双账本并行；周报执行偏离；按 §7 停用规则监控（按策略腿）。

### §11.1 一期成功标准（M5 结束时判定；A股达标为必要条件，美股同口径作观察项）
1. 模型账本 buy 组 T+20 跑赢基准的比例，高于 hold 对照组 ≥ 5 个百分点（**按市场分别统计，各对本市场基准；美股样本 < 30 条不判定**）；
2. 数据管道无人工干预连续运行 ≥ 4 周（含美股链路，sync_failures 自动消化，零手工修数）；
3. 用户日均使用 ≤ 10 分钟（双市场合计）且回填执行率 ≥ 70%；
4. 至少 1 条**策略腿（策略×市场）**实盘期滚动表现落入其回测样本外置信区间（±2σ；美股腿按牛市段口径解释——见 §7 OOS regime 分层）。
四条全过 → 启动二期；否则复盘。

## 12. 风险登记册

| 风险 | 应对 |
|---|---|
| Tushare 积分不足 | T0.1 实测积分需求，预算上限 ¥500/年，超出逐项降级 |
| 回测决策门不过 | 按单规则归因裁剪不硬上；最小预案 = 仅 A股 S1+S2（重归一 67/23 + 10 现金） |
| SEC XBRL tag 映射不齐 / 接口变更 | T0.4 前置验证 + 收入 tag 多重 fallback；失败则 S2 美股腿按 §5.2 fallback 降级 |
| yfinance 限流/字段变更 | T0.4 实测失败率；美股腿允许整体或按策略停用而不影响 A股；`enabled: false` 一键关停 |
| S4 美股 alpha 先验偏弱（假设与 A股不同源，见 §5.2） | 军令状按市场独立执行 + 费后过门不放松 + 试运行滚动 30 信号停用兜底 |
| 美股退市票数据缺失（幸存者偏差残留） | Stooq 补拉 + 排除清单量化标注（对 S4 为收益高估方向） |
| 用户回填率低 | ≤3 次交互 AC + CLI 快捷回填 + 账本失真防护自动降级 |
| H00300/预约披露接口失效 | §3.1 降级链 + 健康检查告警 |
| 单人业余弃坑 | M1 垂直切片 4 周内见到每日产出；任务粒度一晚一个 |

---

## 附录 A · 二期规格存档（本期不实现，实现时以本附录为准）

> A.1（美股腿）已于 v2.1 并入一期正文，v2.2 补齐数据与工程细节（§3.1 #12–20、§5.1.1、§5.2 美股适配、§11 T0.4/T3.6a–c/T3.7a–c），此处不再保留。

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
- 备注：S3/S5 若二期引入 SEC XBRL 历史财务 + 积累的估值快照，可评估美股腿，但需先满足"3 年估值序列"前提。

### A.4 二期其他
看板③个股详情（K线+信号标注）④成绩单图表增强 ⑥回测实验室（提交参数→CLI 子进程→展示 backtest_runs）；A股五策略版配额调整为 S1 25/S2 20/S3 20/S4 10/S5 25（美股维持三策略 40/30/20）；组合级验收扩展（五策略相关矩阵、2021 段熔断专项）；A+美混合净值与汇率折算。

---
*免责声明：本系统输出为规则引擎的机械结果，不构成投资建议。*
