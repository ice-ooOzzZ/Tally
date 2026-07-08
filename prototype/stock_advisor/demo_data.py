# -*- coding: utf-8 -*-
"""离线演示数据：合成不同走势形态的K线 + 假基本面，
用于无网络环境下验证全流程逻辑。
"""
import numpy as np
import pandas as pd

RNG = np.random.default_rng(42)

# (代码, 名称, 市场, 走势形态)
DEMO_STOCKS = [
    ("600001", "演示银行", "CN", "uptrend"),        # 稳步上涨 → 应偏多
    ("600002", "演示白酒", "CN", "breakout"),       # 底部放量突破 → 应触发买入
    ("600003", "演示地产", "CN", "downtrend"),      # 持续阴跌 → 应触发卖出
    ("600004", "演示电力", "CN", "sideways"),       # 横盘 → 应观望
    ("DMAAPL", "Demo Apple", "US", "uptrend_hot"),  # 涨多了RSI超买 → 观望/减分
    ("DMTSLA", "Demo Tesla", "US", "crash"),        # 高位放量跳水 → 应触发卖出
    ("DMKO",   "Demo Cola", "US", "recover"),       # 超跌反弹 → 应偏多
    ("DMXOM",  "Demo Oil", "US", "sideways"),
]


def make_history(pattern: str, days: int = 250) -> pd.DataFrame:
    base = 100.0
    dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=days)
    drift = {
        "uptrend": np.full(days, 0.0012),
        "uptrend_hot": np.concatenate([np.full(days - 30, 0.001), np.full(30, 0.009)]),
        "downtrend": np.full(days, -0.002),
        "sideways": np.zeros(days),
        "breakout": np.concatenate([np.full(days - 15, -0.0008), np.full(15, 0.012)]),
        "crash": np.concatenate([np.full(days - 10, 0.0015), np.full(10, -0.03)]),
        "recover": np.concatenate([np.full(days - 20, -0.0025), np.full(20, 0.008)]),
    }[pattern]
    noise = RNG.normal(0, 0.012, days)
    close = base * np.exp(np.cumsum(drift + noise))

    vol_base = RNG.uniform(0.8e6, 1.2e6)
    vol = RNG.normal(vol_base, vol_base * 0.15, days).clip(min=1e5)
    # 突破/跳水形态在尾部放量
    if pattern in ("breakout", "crash"):
        vol[-10:] *= 2.8

    op = close * (1 + RNG.normal(0, 0.004, days))
    hi = np.maximum(op, close) * (1 + abs(RNG.normal(0, 0.005, days)))
    lo = np.minimum(op, close) * (1 - abs(RNG.normal(0, 0.005, days)))
    return pd.DataFrame({"date": dates, "open": op, "high": hi,
                         "low": lo, "close": close, "volume": vol})


def demo_cn_snapshot() -> pd.DataFrame:
    """伪造A股全市场快照：4只演示股 + 一批不合格的干扰股"""
    rows = []
    for code, name, market, _ in DEMO_STOCKS:
        if market != "CN":
            continue
        rows.append({"code": code, "name": name, "price": 100.0,
                     "pe": RNG.uniform(8, 30), "pb": RNG.uniform(1, 5),
                     "market_cap": RNG.uniform(200e8, 5000e8),
                     "turnover_amt": RNG.uniform(2e8, 50e8)})
    # 干扰股：亏损/微盘/流动性差，应被筛掉
    for i in range(20):
        rows.append({"code": f"30{i:04d}", "name": f"干扰股{i}", "price": 10.0,
                     "pe": RNG.choice([-5, 80, 200]), "pb": RNG.uniform(8, 20),
                     "market_cap": RNG.uniform(5e8, 50e8),
                     "turnover_amt": RNG.uniform(1e6, 5e7)})
    df = pd.DataFrame(rows)
    df["market"] = "CN"
    return df


def demo_us_fundamentals() -> pd.DataFrame:
    rows = []
    for code, name, market, _ in DEMO_STOCKS:
        if market != "US":
            continue
        rows.append({"code": code, "name": name, "price": 100.0,
                     "pe": RNG.uniform(15, 35), "roe": RNG.uniform(0.15, 0.45),
                     "market_cap": RNG.uniform(50e9, 3000e9),
                     "avg_volume": RNG.uniform(5e6, 8e7)})
    # 干扰：高PE低ROE
    rows.append({"code": "DMBAD", "name": "Demo Bad Co", "price": 5.0,
                 "pe": 120.0, "roe": 0.02, "market_cap": 2e9, "avg_volume": 3e5})
    df = pd.DataFrame(rows)
    df["market"] = "US"
    return df


def demo_history(code: str) -> pd.DataFrame:
    for c, _, _, pattern in DEMO_STOCKS:
        if c == code:
            return make_history(pattern)
    return make_history("sideways")
