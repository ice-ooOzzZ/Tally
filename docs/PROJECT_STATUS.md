# Tally 项目状态与恢复指南(RESUME HERE)

> 最后更新:2026-07-09(M1 T1.3 合并后)。**换机/重装后从本文件恢复。**

## 一、这是什么
Tally(明账)= 单用户、本地运行的 **A股 + 美股** 量化选股建议工具。
- 唯一实现依据(single source of truth):**`IMPLEMENTATION_SPEC.md`(当前 v2.2.1)**。
- 需求/评审档:`docs/archive/`(PRD 等)、`docs/design/技术选型评审-2026-07-08.md`。
- 原型(参考,非正式实现):`prototype/stock_advisor/`。

## 二、仓库 / 环境
- GitHub:`git@github.com:ice-ooOzzZ/Tally.git`(SSH),集成分支 **main**。GitHub 账户 `ice-ooOzzZ`;git 提交身份 `Qingzhu Liu <qingzhu.liu@cobo.com>`。
- Python:**必须 3.11–3.12**(`requires-python=">=3.11,<3.13"`),用 **uv** 管理,本地跑在 3.12 venv。
- **恢复命令**:`uv sync --extra dev --extra dashboard`(⚠️ 裸 `uv sync` 会卸载 pytest/ruff/black/mypy——它们在 optional-deps.dev)。
- 门禁(全部要绿):`uv run pytest -q` / `uv run ruff check .` / `uv run black --check .` / `uv run mypy src tests`。
- 依赖注意:`numpy>=1.26,<2.5`(2.5 stub 语法问题)、`yfinance` pin 精确版本。
- **密钥**:`TUSHARE_TOKEN` / `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` 走 `.env`(见 `.env.example`)。当前开发环境**无真实 TUSHARE_TOKEN**,全部测试离线用 fixture(`tests/fixtures/tushare/` 手工构造,回放机制见其 README);跑测试请 `env -u TUSHARE_TOKEN` 验证离线。

## 三、开发流水线(必须遵守)
详见 `docs/DEVELOPMENT_PROCESS.md`。要点:
- 每个模块走 feature 分支 `feat/<里程碑>-<模块>` → 合入 main(`--no-ff`);合并前必写 `docs/changelog/<日期>-<模块>.md`(模板 `docs/changelog/TEMPLATE.md`)。
- **档位**:精简档(默认,M1 T1.2 起)= 1 开发 + 1 合并审查(代码+Python)+ 1 测试 + 1 回归;全量档(关键模块)= 2 审查 + 2 测试 + 2 回归。门禁两档一致:无 CRITICAL/HIGH、AC 全绿、防未来函数。
- **⚠️ 开发/修复 Agent 铁律:禁止派生子 Agent、禁止等待子任务**(曾因此挂死);只做 改代码→自测→commit→回报,审查/测试由主控编排。
- 三条铁律(CLAUDE.md):防未来函数(数据只截断到 as_of_date)/ 回测实盘同源 / 所有持久化经 Repository。strategy/ 禁 `if market ==`(AST 单测把关)。无魔法数字(参数进 config/*.yaml)。

## 四、关键决策(产品负责人已拍板)
- **通知渠道 = Telegram**(非 Slack;spec v2.2.1 已改)。
- **美股 `US.enabled=false`**(config/system.yaml),A股链路稳跑 ≥4 周后再开。
- 技术栈:Python 单栈;**裸 sqlite3 无 ORM**;Streamlit + plotly;typer CLI;launchd 调度(两份 CN/US);**不引** FastAPI/Redis/SQLAlchemy/Celery/APScheduler。多市场差异走 **MarketProfile + market_overrides 深合并**。
- SEC EDGAR User-Agent 含个人邮箱=PII,已登记"产品化前待处理"。

## 五、已完成(main 上,全部经流水线 + 回归)
| 里程碑 | 内容 | 合并 commit |
|---|---|---|
| 流程基础设施 | 流水线 + 改动文档模板 | `2afb5e5` |
| M0.5 工程脚手架 | pyproject/uv、pydantic 配置层、calendar、MarketProfile、synth K线生成器、CLAUDE.md | `2550c26` |
| M1 T1.1 | 最小 Repository(kline/valuation/signals)+ WAL 单写线程 | `0d0b35f` |
| M1 T1.2 | Tushare 适配器(daily/adj_factor/daily_basic)+ 令牌桶 RateLimiterRegistry + fixture 回放 + 缺 token loud failure | `6c43e0d` |
| M1 T1.3 | 最小同步管道(手工名单 config + 增量 + as_of_date 防未来函数 + 跨表自愈) | `3e63124` |

测试基线:**273 passed**(离线)。已建目录:`src/tally/{common,data,data/sources,strategy(空),portfolio(空),pool(空),backtest(空),tracking(空)}`。

## 六、下一步(恢复后按此顺序)
0. **⚠️ 头号待办 · 修 flaky**:`tests/data/test_writer.py::test_fatal_exception_also_drains_and_fails_other_already_queued_tasks`——隔离连跑 5 次约 2 通过/3 失败,T1.1 WriteQueue 线程竞态(疑似测试 5s join 窗口过紧或入队时序竞态)。单开 `feat/fix-writer-flaky` 走流水线修掉,再继续。
1. **T1.4** `strategy/indicators.py`:SMA/ATR/RSI/动量,**动态复权**(kline 已带 adj_factor,经 `Repository.get_kline(code,market,start,end)` 升序读出即可)。AC:与手算已知值对照单测。
2. **T1.5** S1 趋势突破策略 + Strategy 基类 + 持仓状态机。AC:用 `tests/synth/generators.py` 六形态方向单测(breakout 触发买入、crash/downtrend 不触发、吊灯止损退出)。**严守 strategy/ 禁 `if market ==`**(AST 单测已在 `tests/test_no_market_literal_in_strategy.py`)。
3. **T1.6** 每日 CLI `tally run --market CN --until today` → markdown 信号报告 + **Telegram 推送**;顺带接顶层装配(读 system.yaml/watchlist.yaml 构造 SyncEngine)+ 数据源层"缺失密钥 loud failure"。AC:幂等;报告含"未经回测验证,仅观察"。
4. 之后 M2(数据层完备 + 回测基建 + S1 过门)→ M3(S2/S4 + 美股接入)→ M4 → M5,见 spec §11。

## 七、恢复检查清单
```bash
git clone git@github.com:ice-ooOzzZ/Tally.git && cd Tally
uv sync --extra dev --extra dashboard
env -u TUSHARE_TOKEN uv run pytest -q         # 应 273 passed(先修 flaky 后应稳定)
# 读:docs/PROJECT_STATUS.md(本文件)→ docs/DEVELOPMENT_PROCESS.md → docs/changelog/(按时间) → IMPLEMENTATION_SPEC.md §11
```
