# -*- coding: utf-8 -*-
"""Markdown 报告生成"""
import os
from datetime import datetime

from config import REPORT_DIR


def _section(title, results):
    if not results:
        return f"### {title}\n\n（无）\n"
    lines = [f"### {title}\n"]
    for r in sorted(results, key=lambda x: -abs(x["score"])):
        s = r["snapshot"]
        lines.append(
            f"**{r['name']}（{r['code']}，{r['market']}）** 得分 {r['score']:+d}　"
            f"现价 {s.get('close', '?')}　RSI {s.get('rsi', '?')}　量比 {s.get('vol_ratio', '?')}")
        for reason in r["reasons"]:
            lines.append(f"- {reason}")
        lines.append("")
    return "\n".join(lines)


def daily_report(results: list, pool_info: dict = None, screen_changes: dict = None) -> str:
    """生成每日报告，返回文件路径"""
    today = datetime.now().strftime("%Y-%m-%d")
    buys = [r for r in results if r["advice"] == "建议买入"]
    sells = [r for r in results if r["advice"] == "建议卖出"]
    holds = [r for r in results if r["advice"] == "观望"]

    md = [f"# 每日选股分析报告 — {today}\n",
          f"股票池共 {len(results)} 只｜建议买入 {len(buys)}｜建议卖出 {len(sells)}｜观望 {len(holds)}\n"]

    if screen_changes and (screen_changes["added"] or screen_changes["removed"]):
        md.append("## 本周股票池变动\n")
        for a in screen_changes["added"]:
            md.append(f"- 🟢 入池：{a}")
        for r in screen_changes["removed"]:
            md.append(f"- 🔴 出池：{r}")
        md.append("")

    md.append("## 交易信号\n")
    md.append(_section("🟢 建议买入", buys))
    md.append(_section("🔴 建议卖出", sells))

    md.append("### ⚪ 观望\n")
    if holds:
        md.append("| 股票 | 得分 | 现价 | 主要信号 |")
        md.append("|---|---|---|---|")
        for r in sorted(holds, key=lambda x: -x["score"]):
            first = r["reasons"][0] if r["reasons"] else ""
            md.append(f"| {r['name']}({r['code']}) | {r['score']:+d} | "
                      f"{r['snapshot'].get('close', '?')} | {first} |")
    else:
        md.append("（无）")

    md.append("\n---\n> 免责声明：本报告由程序基于公开数据自动生成，仅为技术/基本面规则的机械输出，"
              "不构成投资建议。历史规律不代表未来表现，据此操作风险自负。")

    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"report_{today}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return path
