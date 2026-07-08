# 股票分析助手（原型）

自动化选股工具：**每周**全市场基本面筛选维护股票池（入池/出池），**每日**对池内股票跑技术面信号，生成"建议买入/卖出 + 原因"的 Markdown 报告。覆盖 A股（AkShare/东方财富数据）+ 美股（yfinance）。

## 快速开始

```bash
pip install -r requirements.txt

# 1. 先跑离线演示，验证环境（不需要网络拉行情）
python main.py demo

# 2. 每周跑一次：全市场筛选，更新股票池
python main.py screen

# 3. 每日收盘后跑：生成当日买卖信号报告
python main.py daily
```

报告输出在 `reports/report_YYYY-MM-DD.md`，股票池在 `data/pool.json`。

## 定时运行

macOS/Linux 用 crontab（`crontab -e`）：

```cron
# 每周一早上8点更新股票池
0 8 * * 1 cd /path/to/stock_advisor && python3 main.py screen >> run.log 2>&1
# 每个交易日下午4点（A股收盘后）生成日报
0 16 * * 1-5 cd /path/to/stock_advisor && python3 main.py daily >> run.log 2>&1
```

Windows 用「任务计划程序」建两个对应的计划任务即可。

## 工作原理

**每周入池（screener.py）** — 基本面硬条件筛选，标准在 `config.py`：

| | A股 | 美股 |
|---|---|---|
| 市值 | ≥100亿元 | ≥100亿美元 |
| PE | 0～40 | 0～40 |
| 其他 | PB<8，日成交≥1亿 | ROE≥12%，日均量≥100万股 |
| 池大小 | 按市值取前50 | 前30 |

出池有 2 周宽限期，避免临界股票反复进出。

**每日信号（signals.py）** — 多规则打分，每条命中都会写进报告作为"原因"：

| 规则 | 加分 | 减分 |
|---|---|---|
| 均线 | 金叉+2 / 多头排列+1 | 死叉-2 / 空头排列-1 |
| MA20位置 | 站上向上的MA20 +1 | 跌破向下的MA20 -1 |
| MACD | 近3日金叉 +2 | 近3日死叉 -2 |
| RSI | 超卖回升 +1 | 超买(>75) -1 |
| 量价 | 放量上涨 +1 | 放量下跌 -2 |
| 急跌 | — | 10日跌超15% -2 |
| 年度位置 | 低位企稳 +1 | 创250日新低 -1 |

总分 ≥ +3 建议买入，≤ -3 建议卖出，中间观望。阈值和所有参数在 `config.py` 里改。

## 文件结构

```
config.py       所有可调参数（入池标准、指标参数、阈值）
data_sources.py A股/美股数据拉取（akshare + yfinance）
screener.py     股票池入池/出池逻辑
signals.py      技术指标计算 + 打分 + 原因生成
report.py       Markdown 日报生成
main.py         入口（screen / daily / demo 三种模式）
demo_data.py    离线合成数据（演示与测试用）
```

## 重要提醒

1. **这是规则的机械输出，不是投资建议。** 上实盘参考前，强烈建议先做历史回测验证胜率和盈亏比（可以作为下一步迭代）。
2. 免费数据源有限频，`screen` 模式拉美股基本面较慢（标普500约需几分钟）；`config.py` 的 `REQUEST_DELAY` 可调。
3. 首次使用顺序：先 `screen` 建池，再 `daily` 出报告。
4. 想缩小范围可直接手工编辑 `data/pool.json` 添加自选股。

## 下一步可以迭代的方向

回测框架（验证策略历史表现）、A股 ROE 等深度财务因子入池、报告推送（邮件/微信/钉钉）、仓位建议与止损价计算。
