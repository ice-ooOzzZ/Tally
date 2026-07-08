# -*- coding: utf-8 -*-
"""数据源封装：A股用 AkShare（东财），美股用 yfinance。
统一输出格式，便于上层模块不区分市场。
"""
import time
import pandas as pd

from config import REQUEST_DELAY, US_FALLBACK_TICKERS, SIGNAL_PARAMS


# ---------- A股 ----------

def cn_market_snapshot() -> pd.DataFrame:
    """全A股实时快照，含 PE/PB/市值/成交额，用于入池初筛。
    返回统一列: code, name, price, pe, pb, market_cap, turnover_amt
    """
    import akshare as ak
    df = ak.stock_zh_a_spot_em()
    out = pd.DataFrame({
        "code": df["代码"],
        "name": df["名称"],
        "price": pd.to_numeric(df["最新价"], errors="coerce"),
        "pe": pd.to_numeric(df["市盈率-动态"], errors="coerce"),
        "pb": pd.to_numeric(df["市净率"], errors="coerce"),
        "market_cap": pd.to_numeric(df["总市值"], errors="coerce"),
        "turnover_amt": pd.to_numeric(df["成交额"], errors="coerce"),
    })
    out["market"] = "CN"
    return out.dropna(subset=["price"])


def cn_history(code: str, days: int = None) -> pd.DataFrame:
    """A股日K（前复权）。返回统一列: date, open, high, low, close, volume"""
    import akshare as ak
    days = days or SIGNAL_PARAMS["history_days"]
    start = (pd.Timestamp.now() - pd.Timedelta(days=days * 1.6)).strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, adjust="qfq")
    time.sleep(REQUEST_DELAY)
    out = pd.DataFrame({
        "date": pd.to_datetime(df["日期"]),
        "open": df["开盘"], "high": df["最高"], "low": df["最低"],
        "close": df["收盘"], "volume": df["成交量"],
    })
    return out.tail(days).reset_index(drop=True)


# ---------- 美股 ----------

def us_candidate_tickers() -> list:
    """标普500成分股列表；拉取失败退回备用大盘股列表。"""
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        return [t.replace(".", "-") for t in tables[0]["Symbol"].tolist()]
    except Exception:
        return US_FALLBACK_TICKERS


def us_fundamentals(tickers: list) -> pd.DataFrame:
    """逐只拉取美股基本面（yfinance .info，较慢）。
    返回统一列: code, name, price, pe, roe, market_cap, avg_volume
    """
    import yfinance as yf
    rows = []
    for t in tickers:
        try:
            info = yf.Ticker(t).info
            rows.append({
                "code": t,
                "name": info.get("shortName", t),
                "price": info.get("currentPrice"),
                "pe": info.get("trailingPE"),
                "roe": info.get("returnOnEquity"),
                "market_cap": info.get("marketCap"),
                "avg_volume": info.get("averageVolume"),
            })
        except Exception as e:
            print(f"  [warn] {t} 基本面拉取失败: {e}")
        time.sleep(REQUEST_DELAY)
    df = pd.DataFrame(rows)
    df["market"] = "US"
    return df


def us_history(ticker: str, days: int = None) -> pd.DataFrame:
    """美股日K。返回统一列: date, open, high, low, close, volume"""
    import yfinance as yf
    days = days or SIGNAL_PARAMS["history_days"]
    df = yf.Ticker(ticker).history(period=f"{int(days * 1.6)}d")
    time.sleep(REQUEST_DELAY)
    df = df.reset_index()
    out = pd.DataFrame({
        "date": pd.to_datetime(df["Date"]).dt.tz_localize(None),
        "open": df["Open"], "high": df["High"], "low": df["Low"],
        "close": df["Close"], "volume": df["Volume"],
    })
    return out.tail(days).reset_index(drop=True)


# ---------- 统一入口 ----------

def get_history(code: str, market: str) -> pd.DataFrame:
    return cn_history(code) if market == "CN" else us_history(code)
