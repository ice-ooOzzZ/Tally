# -*- coding: utf-8 -*-
"""股票分析助手 — 入口

用法:
  python main.py screen        # 每周：全市场筛选，更新股票池（入池/出池）
  python main.py daily         # 每日：对池内股票跑技术信号，生成买卖建议报告
  python main.py demo          # 离线演示：用合成数据跑通 screen + daily 全流程
"""
import sys

import pandas as pd

import screener
import signals
import report
from screener import load_pool


def run_screen(demo=False):
    print("== 股票池筛选（每周） ==")
    if demo:
        import demo_data
        cn_snap = demo_data.demo_cn_snapshot()
        us_fund = demo_data.demo_us_fundamentals()
    else:
        import data_sources as ds
        print("拉取A股全市场快照...")
        cn_snap = ds.cn_market_snapshot()
        print(f"  共 {len(cn_snap)} 只")
        print("拉取美股候选基本面（较慢，约几分钟）...")
        tickers = ds.us_candidate_tickers()
        us_fund = ds.us_fundamentals(tickers)

    cn_pass = screener.screen_cn(cn_snap)
    us_pass = screener.screen_us(us_fund)
    print(f"A股入围 {len(cn_pass)} 只，美股入围 {len(us_pass)} 只")

    qualified = pd.concat([
        cn_pass[["code", "name", "market", "reason"]],
        us_pass[["code", "name", "market", "reason"]],
    ], ignore_index=True)
    changes = screener.update_pool(qualified)
    print(f"本次入池 {len(changes['added'])} 只，出池 {len(changes['removed'])} 只，"
          f"池内共 {len(changes['pool']['stocks'])} 只")
    return changes


def run_daily(demo=False, screen_changes=None):
    print("== 每日信号分析 ==")
    pool = load_pool()
    stocks = pool["stocks"]
    if not stocks:
        print("股票池为空，请先运行: python main.py screen")
        return

    if demo:
        import demo_data
        get_history = lambda code, market: demo_data.demo_history(code)
    else:
        import data_sources as ds
        get_history = ds.get_history

    results = []
    for key, s in stocks.items():
        try:
            hist = get_history(s["code"], s["market"])
            r = signals.evaluate(hist)
            r.update({"code": s["code"], "name": s["name"], "market": s["market"]})
            results.append(r)
            print(f"  {s['name']}({s['code']}): {r['advice']} (得分 {r['score']:+d})")
        except Exception as e:
            print(f"  [warn] {s['name']}({s['code']}) 分析失败: {e}")

    path = report.daily_report(results, pool, screen_changes)
    print(f"\n报告已生成: {path}")
    return path


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if mode == "screen":
        run_screen()
    elif mode == "daily":
        run_daily()
    elif mode == "demo":
        changes = run_screen(demo=True)
        run_daily(demo=True, screen_changes=changes)
    else:
        print(__doc__)
