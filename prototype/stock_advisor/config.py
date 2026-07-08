# -*- coding: utf-8 -*-
"""全局配置：入池标准、技术参数、运行选项"""

# ========== 股票池入池标准（每周跑一次 screen） ==========

# A股入池硬条件（基于东财实时快照字段）
CN_POOL_CRITERIA = {
    "min_market_cap": 100e8,     # 总市值 >= 100亿（元）
    "pe_range": (0, 40),         # 动态PE在 (0, 40]，剔除亏损和高估
    "max_pb": 8,                 # 市净率 < 8
    "min_turnover_amt": 1e8,     # 日成交额 >= 1亿，保证流动性
}
CN_POOL_SIZE = 50                # 按市值排序取前 N 只入池

# 美股入池硬条件（基于 yfinance info 字段）
US_POOL_CRITERIA = {
    "min_market_cap": 10e9,      # 市值 >= 100亿美元
    "pe_range": (0, 40),         # trailing PE
    "min_roe": 0.12,             # ROE >= 12%
    "min_avg_volume": 1e6,       # 日均成交量 >= 100万股
}
US_POOL_SIZE = 30

# 美股候选范围：默认标普500成分股（从维基百科拉取）；失败时退回下面的备用列表
US_FALLBACK_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO",
    "BRK-B", "JPM", "V", "MA", "UNH", "JNJ", "XOM", "PG", "HD", "COST",
    "ABBV", "KO", "PEP", "MRK", "WMT", "CSCO", "ORCL", "CRM", "AMD",
    "NFLX", "ADBE", "QCOM",
]

# 出池条件：连续 N 周不满足入池硬条件则移出
POOL_EXIT_GRACE_WEEKS = 2

# ========== 技术面信号参数（每日跑 daily） ==========

SIGNAL_PARAMS = {
    "ma_fast": 20,               # 快均线
    "ma_slow": 60,               # 慢均线
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 75,
    "vol_ratio_threshold": 1.8,  # 放量阈值：当日量 / 20日均量
    "macd": (12, 26, 9),
    "history_days": 250,         # 拉取历史K线天数
}

# 信号打分阈值：总分 >= buy_threshold 给"建议买入"，<= sell_threshold 给"建议卖出"
BUY_THRESHOLD = 3
SELL_THRESHOLD = -3

# ========== 运行选项 ==========

import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")      # 池子、缓存
REPORT_DIR = os.path.join(BASE_DIR, "reports") # 每日报告
POOL_FILE = os.path.join(DATA_DIR, "pool.json")

REQUEST_DELAY = 0.3   # 每次请求间隔秒数，避免被限频
