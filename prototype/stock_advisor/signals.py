# -*- coding: utf-8 -*-
"""技术面信号引擎：对单只股票的日K计算指标，多规则打分，
输出 建议(买入/卖出/观望) + 得分 + 每条触发原因。

打分规则（每条命中记 + / - 分）：
  +2 MA20 上穿 MA60（近5日金叉）        -2 MA20 下穿 MA60（近5日死叉）
  +1 收盘价站上 MA20 且 MA20 向上        -1 收盘价跌破 MA20 且 MA20 向下
  +2 MACD 金叉（近3日）                  -2 MACD 死叉（近3日）
  +1 RSI 从超卖区回升                    -1 RSI 超买
  +1 放量上涨（量比>阈值且当日收涨）      -2 放量下跌
  +1 价格处于250日区间下沿(<30%分位)且企稳 -1 价格创250日新低
总分 >= BUY_THRESHOLD → 建议买入；<= SELL_THRESHOLD → 建议卖出；否则观望。
"""
import pandas as pd

from config import SIGNAL_PARAMS, BUY_THRESHOLD, SELL_THRESHOLD

P = SIGNAL_PARAMS


# ---------- 指标计算 ----------

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ma_fast"] = df["close"].rolling(P["ma_fast"]).mean()
    df["ma_slow"] = df["close"].rolling(P["ma_slow"]).mean()

    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(P["rsi_period"]).mean()
    loss = (-delta.clip(upper=0)).rolling(P["rsi_period"]).mean()
    rs = gain / loss.replace(0, 1e-9)
    df["rsi"] = 100 - 100 / (1 + rs)

    # MACD
    fast, slow, sig = P["macd"]
    ema_f = df["close"].ewm(span=fast, adjust=False).mean()
    ema_s = df["close"].ewm(span=slow, adjust=False).mean()
    df["dif"] = ema_f - ema_s
    df["dea"] = df["dif"].ewm(span=sig, adjust=False).mean()

    # 量比：当日量 / 20日均量
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma20"]

    # 250日价格分位
    roll_min = df["close"].rolling(250, min_periods=60).min()
    roll_max = df["close"].rolling(250, min_periods=60).max()
    df["pos_250"] = (df["close"] - roll_min) / (roll_max - roll_min).replace(0, 1e-9)
    return df


def _crossed(a: pd.Series, b: pd.Series, lookback: int) -> bool:
    """近 lookback 日内 a 是否上穿 b"""
    above = (a > b).astype(bool)
    prev_above = above.shift(1).astype("boolean").fillna(False).astype(bool)
    return bool((above & ~prev_above).tail(lookback).any())


# ---------- 信号判定 ----------

def evaluate(df: pd.DataFrame) -> dict:
    """输入含指标的日K，返回 {advice, score, reasons: [...], snapshot: {...}}"""
    df = add_indicators(df)
    if len(df) < P["ma_slow"] + 5:
        return {"advice": "数据不足", "score": 0, "reasons": ["历史数据不足以计算慢均线"],
                "snapshot": {}}

    last = df.iloc[-1]
    prev = df.iloc[-2]
    score, reasons = 0, []

    # 1. 均线趋势：拐点（金叉/死叉）+ 状态（多头/空头排列）
    if _crossed(df["ma_fast"], df["ma_slow"], 5):
        score += 2; reasons.append("+2 MA20近5日上穿MA60（金叉），中期趋势转多")
    elif _crossed(df["ma_slow"], df["ma_fast"], 5):
        score -= 2; reasons.append("-2 MA20近5日下穿MA60（死叉），中期趋势转空")
    elif last["close"] > last["ma_fast"] > last["ma_slow"]:
        score += 1; reasons.append("+1 多头排列（价>MA20>MA60），上升趋势延续中")
    elif last["close"] < last["ma_fast"] < last["ma_slow"]:
        score -= 1; reasons.append("-1 空头排列（价<MA20<MA60），下跌趋势延续中")

    ma_fast_rising = last["ma_fast"] > prev["ma_fast"]
    if last["close"] > last["ma_fast"] and ma_fast_rising:
        score += 1; reasons.append("+1 收盘价站上向上的MA20，短期强势")
    elif last["close"] < last["ma_fast"] and not ma_fast_rising:
        score -= 1; reasons.append("-1 收盘价跌破向下的MA20，短期走弱")

    # 2. MACD
    if _crossed(df["dif"], df["dea"], 3):
        score += 2; reasons.append("+2 MACD近3日金叉，动能转强")
    elif _crossed(df["dea"], df["dif"], 3):
        score -= 2; reasons.append("-2 MACD近3日死叉，动能转弱")

    # 3. RSI
    recent_rsi = df["rsi"].tail(5)
    if recent_rsi.min() < P["rsi_oversold"] and last["rsi"] > recent_rsi.min():
        score += 1; reasons.append(
            f"+1 RSI从超卖区({recent_rsi.min():.0f})回升至{last['rsi']:.0f}，超跌反弹")
    elif last["rsi"] > P["rsi_overbought"]:
        score -= 1; reasons.append(f"-1 RSI {last['rsi']:.0f} 超买，短期回调风险")

    # 4. 量价配合：放量方向按近3日涨跌判断，避免被单日波动误导
    if last["vol_ratio"] > P["vol_ratio_threshold"]:
        chg_3d = last["close"] / df["close"].iloc[-4] - 1
        if chg_3d > 0:
            score += 1; reasons.append(
                f"+1 放量上涨（量比{last['vol_ratio']:.1f}，3日涨{chg_3d * 100:.1f}%），资金流入")
        else:
            score -= 2; reasons.append(
                f"-2 放量下跌（量比{last['vol_ratio']:.1f}，3日跌{-chg_3d * 100:.1f}%），资金出逃")

    # 5. 短期急跌动量：近10日跌幅超过15%，无论其他指标如何都应警惕
    chg_10d = last["close"] / df["close"].iloc[-11] - 1
    if chg_10d < -0.15:
        score -= 2; reasons.append(f"-2 近10日累计下跌{-chg_10d * 100:.0f}%，急跌破位，风险优先")

    # 6. 年度价格位置
    if last["pos_250"] < 0.30 and last["close"] > prev["close"]:
        score += 1; reasons.append(
            f"+1 价格处于年度区间低位({last['pos_250'] * 100:.0f}%分位)且当日收涨，具备安全边际")
    elif last["pos_250"] <= 0.02:
        score -= 1; reasons.append("-1 价格创250日新低，下跌趋势未止")

    advice = "建议买入" if score >= BUY_THRESHOLD else (
        "建议卖出" if score <= SELL_THRESHOLD else "观望")

    return {
        "advice": advice, "score": score,
        "reasons": reasons or ["无明显信号"],
        "snapshot": {
            "close": round(float(last["close"]), 2),
            "ma20": round(float(last["ma_fast"]), 2),
            "ma60": round(float(last["ma_slow"]), 2),
            "rsi": round(float(last["rsi"]), 1),
            "vol_ratio": round(float(last["vol_ratio"]), 2),
            "pos_250": round(float(last["pos_250"]), 2),
        },
    }
