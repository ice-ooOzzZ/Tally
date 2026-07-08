# -*- coding: utf-8 -*-
"""股票池管理：每周跑一次。
入池：满足基本面硬条件，按市值排序取前 N。
出池：连续 POOL_EXIT_GRACE_WEEKS 周不满足条件则移出（避免临界抖动）。
池子持久化在 data/pool.json。
"""
import json
import os
from datetime import date

import pandas as pd

from config import (CN_POOL_CRITERIA, CN_POOL_SIZE, US_POOL_CRITERIA,
                    US_POOL_SIZE, POOL_EXIT_GRACE_WEEKS, POOL_FILE, DATA_DIR)


def load_pool() -> dict:
    if os.path.exists(POOL_FILE):
        with open(POOL_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"stocks": {}, "last_screen": None}


def save_pool(pool: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(POOL_FILE, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)


def screen_cn(snapshot: pd.DataFrame) -> pd.DataFrame:
    """A股入池筛选，返回合格名单（含入池理由）"""
    c = CN_POOL_CRITERIA
    df = snapshot.copy()
    mask = (
        (df["market_cap"] >= c["min_market_cap"])
        & (df["pe"] > c["pe_range"][0]) & (df["pe"] <= c["pe_range"][1])
        & (df["pb"] > 0) & (df["pb"] < c["max_pb"])
        & (df["turnover_amt"] >= c["min_turnover_amt"])
    )
    passed = df[mask].sort_values("market_cap", ascending=False).head(CN_POOL_SIZE)
    passed = passed.copy()
    passed["reason"] = passed.apply(
        lambda r: (f"市值{r.market_cap / 1e8:.0f}亿, PE {r.pe:.1f}, "
                   f"PB {r.pb:.1f}, 日成交{r.turnover_amt / 1e8:.1f}亿"), axis=1)
    return passed


def screen_us(fundamentals: pd.DataFrame) -> pd.DataFrame:
    """美股入池筛选"""
    c = US_POOL_CRITERIA
    df = fundamentals.dropna(subset=["market_cap", "pe"]).copy()
    df["roe"] = df["roe"].fillna(0)
    mask = (
        (df["market_cap"] >= c["min_market_cap"])
        & (df["pe"] > c["pe_range"][0]) & (df["pe"] <= c["pe_range"][1])
        & (df["roe"] >= c["min_roe"])
        & (df["avg_volume"].fillna(0) >= c["min_avg_volume"])
    )
    passed = df[mask].sort_values("market_cap", ascending=False).head(US_POOL_SIZE)
    passed = passed.copy()
    passed["reason"] = passed.apply(
        lambda r: (f"市值${r.market_cap / 1e9:.0f}B, PE {r.pe:.1f}, "
                   f"ROE {r.roe * 100:.0f}%"), axis=1)
    return passed


def update_pool(qualified: pd.DataFrame) -> dict:
    """用本周合格名单更新池子。返回 {added: [...], removed: [...], pool: {...}}"""
    pool = load_pool()
    stocks = pool["stocks"]
    today = str(date.today())
    qualified_keys = set(qualified["market"] + ":" + qualified["code"].astype(str))

    added, removed = [], []

    # 入池 / 重置失格计数
    for _, r in qualified.iterrows():
        key = f"{r.market}:{r.code}"
        if key not in stocks:
            stocks[key] = {"code": str(r.code), "name": r["name"], "market": r.market,
                           "entered": today, "entry_reason": r.reason, "miss_weeks": 0}
            added.append(f"{r['name']}({r.code}) — {r.reason}")
        else:
            stocks[key]["miss_weeks"] = 0

    # 出池：连续 miss 达到宽限周数
    for key in list(stocks.keys()):
        if key not in qualified_keys:
            stocks[key]["miss_weeks"] = stocks[key].get("miss_weeks", 0) + 1
            if stocks[key]["miss_weeks"] >= POOL_EXIT_GRACE_WEEKS:
                s = stocks.pop(key)
                removed.append(f"{s['name']}({s['code']}) — 连续{POOL_EXIT_GRACE_WEEKS}周不满足入池标准")

    pool["last_screen"] = today
    save_pool(pool)
    return {"added": added, "removed": removed, "pool": pool}
