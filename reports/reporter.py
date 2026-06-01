# -*- coding: utf-8 -*-
"""
reporter.py - QuantBot 日报生成模块
从 daily_runner.py 提取，适配 unified_runner.py 输出格式。

用法:
    from reports.reporter import generate_daily_report
    report_path = generate_daily_report(
        date='2026-05-21',
        signals=signals,
        orders=orders,
        positions=positions,
        account=account,
        market_report=market_report,
    )
"""
import sys, os, json
from pathlib import Path
from datetime import datetime

# stdout 编码修正（Windows GBK 环境下 emoji 会炸）
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

BASE = Path('E:/quant')
REPORT_DIR = BASE / 'reports'  # 日报输出目录
MAX_POSITIONS = 5

# 风控阈值（与 config.py 对齐）
RISK_CONFIG = {
    'max_total_position': 0.80,
    'max_drawdown': -0.30,
    'max_daily_loss': -0.05,
}


def generate_daily_report(date: str, signals: list, orders: list = None,
                          positions: list = None, account: dict = None,
                          market_report: dict = None) -> str:
    """
    生成 Markdown 日报。

    参数:
        date: 日期字符串 'YYYY-MM-DD'
        signals: 信号列表，每个信号含 Symbol, fusion_level, fusion_score, fusion_confidence, ...
        orders: 当日订单列表
        positions: 持仓列表，每个持仓含 symbol, shares, entry_price, current_price, pnl, ...
        account: 账户信息 dict, 含 total_assets, cash, market_val
        market_report: 市场状态报告 dict

    返回:
        report_file: 日报文件路径
    """
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REPORT_DIR / f'{date}.md'

    # ── 信号统计 ──────────────────────────────────────────
    strong_buys = [s for s in signals if s.get('fusion_level') == 'STRONG_BUY']
    buys = [s for s in signals if s.get('fusion_level') == 'BUY']
    holds = [s for s in signals if s.get('fusion_level') == 'HOLD']
    sells = [s for s in signals if s.get('fusion_level') in ('SELL', 'STRONG_SELL')]
    reduced = [s for s in signals if s.get('fusion_level') == 'REDUCED']

    # ── 账户概览 ──────────────────────────────────────────
    if account:
        total_assets = account.get('total_assets', 0)
        cash = account.get('cash', 0)
        pos_value = account.get('market_val', 0)
        n_positions = len(positions or [])
    elif positions:
        pos_value = sum(p.get('shares', 0) * p.get('current_price', p.get('entry_price', 0))
                       for p in positions)
        cash = account.get('cash', 0) if account else 0
        total_assets = cash + pos_value
        n_positions = len(positions)
    else:
        total_assets = cash = pos_value = n_positions = 0

    # ── 市场状态 ──────────────────────────────────────────
    ms = market_report or {}
    market_state = ms.get('market_state', 'CRAB') if isinstance(ms, dict) else 'CRAB'
    vix_regime = ms.get('vix_regime', 'CANDIDATE') if isinstance(ms, dict) else 'CANDIDATE'
    trend = ms.get('trend', 'N/A') if isinstance(ms, dict) else 'N/A'
    momentum = ms.get('momentum', 'N/A') if isinstance(ms, dict) else 'N/A'

    # ── 今日交易统计 ──────────────────────────────────────
    today_orders = orders or []
    buy_trades = [o for o in today_orders if o.get('action') == 'BUY']
    sell_trades = [o for o in today_orders if o.get('action') in ('SELL', 'STOP', 'SIGNAL_EXIT')]

    # ── 生成 Markdown ─────────────────────────────────────
    md = f"""# QuantBot 纸交易日报 - {date}

## 📊 账户概览

| 指标 | 数值 |
|------|------|
| **总资产** | {total_assets:,.0f} |
| **现金** | {cash:,.0f} ({f'{cash / total_assets:.1%}' if total_assets > 0 else 'N/A'}) |
| **持仓市值** | {pos_value:,.0f} ({f'{pos_value / total_assets:.1%}' if total_assets > 0 else 'N/A'}) |
| **持仓数** | {n_positions}/{MAX_POSITIONS} |
| **日交易** | {len(today_orders)} 笔 |
| **买入** | {len(buy_trades)} 笔 |
| **卖出** | {len(sell_trades)} 笔 |

## 🎯 市场状态

| 维度 | 状态 |
|------|------|
| **市场状态** | {market_state} |
| **VIX 环境** | {vix_regime} |
| **趋势** | {trend} |
| **动量** | {momentum} |

## 📈 信号汇总

| 信号类型 | 数量 |
|---------|------|
| STRONG_BUY | {len(strong_buys)} |
| BUY | {len(buys)} |
| HOLD | {len(holds)} |
| SELL | {len(sells)} |
| REDUCED | {len(reduced)} |

"""

    # ── 数据新鲜度 ⚠️ ──────────────────────────────────────
    stale_signals = [s for s in signals if s.get('warnings') and any('滞后' in w for w in s.get('warnings', []))]
    if stale_signals:
        md += """## ⚠️ 数据新鲜度告警

| 标的 | 警告内容 |
|------|----------|
"""
        for s in stale_signals:
            warns = [w for w in s.get('warnings', []) if '滞后' in w]
            for w in warns:
                md += f"| {s.get('symbol', '?')} | {w} |\n"
        md += "\n"

    # ── TOP BUY 信号 ──────────────────────────────────────
    top_buys = sorted(strong_buys + buys,
                      key=lambda s: s.get('fusion_score', 0), reverse=True)[:5]
    if top_buys:
        md += """## 🟢 TOP BUY 信号

| 标的 | 信号 | 分数 | 置信度 | RSI 日 | 建议仓位 |
|------|------|------|--------|--------|----------|
"""
        for s in top_buys:
            md += f"| {s.get('symbol', '?')} | {s.get('fusion_level', '?')} | {s.get('fusion_score', 0):+.1f} | {s.get('fusion_confidence', 0):.0%} | {s.get('rsi_daily', 0):.1f} | {s.get('target_position', 0):.0%} |\n"
        md += "\n"

    # ── SELL 信号 ─────────────────────────────────────────
    if sells:
        md += """## 🔴 SELL 信号

| 标的 | 信号 | 分数 | 风险等级 |
|------|------|------|----------|
"""
        for s in sells:
            md += f"| {s.get('symbol', '?')} | {s.get('fusion_level', '?')} | {s.get('fusion_score', 0):+.1f} | {s.get('risk', 'N/A')} |\n"
        md += "\n"

    # ── 持仓明细 ──────────────────────────────────────────
    if positions:
        md += """## 💼 持仓明细

| 标的 | 股数 | 成本价 | 现价 | PnL | 止损价 |
|------|------|--------|------|-----|--------|
"""
        for p in positions:
            pnl = p.get('pnl', (p.get('current_price', 0) / p.get('entry_price', 1) - 1) * 100)
            pnl_color = "🟢" if pnl >= 0 else "🔴"
            stop = p.get('stop_fixed', p.get('stop_loss', 0))
            md += f"| {p.get('symbol', '?')} | {p.get('qty', p.get('shares', 0))} | {p.get('entry_price', p.get('cost_price', 0)):.2f} | {p.get('current_price', 0):.2f} | {pnl_color} {pnl:+.2f}% | {stop:.2f} |\n"
        md += "\n"

    # ── 今日交易 ──────────────────────────────────────────
    if today_orders:
        md += """## 📝 今日交易

"""
        for o in today_orders:
            cost = o.get('qty', 0) * o.get('price', 0)
            pnl_str = f" (PnL: {o.get('pnl', 0):+.2f}%)" if 'pnl' in o else ""
            reason = o.get('reason', '')
            md += f"- **{o['action']}** {o.get('symbol', '?')} @ {o.get('price', 0):.2f} x {o.get('qty', 0)} = {cost:,.0f}{pnl_str}\n"
            if reason:
                md += f"  - {reason}\n"
        md += "\n"

    # ── 风控检查 ──────────────────────────────────────────
    md += """## ⚠️ 风控检查

"""
    risk_issues = []
    if total_assets > 0 and pos_value / total_assets > RISK_CONFIG['max_total_position']:
        risk_issues.append(f"总仓位过高: {pos_value / total_assets:.1%} > {RISK_CONFIG['max_total_position']:.0%}")

    if positions:
        for p in positions:
            if p.get('triggered_stop'):
                risk_issues.append(f"{p.get('symbol', '?')} 触发止损: {p.get('stop_reason', 'N/A')}")

    if risk_issues:
        for issue in risk_issues:
            md += f"- ❌ {issue}\n"
    else:
        md += "- ✅ 无风控触发\n"
    md += "\n"

    # ── 明日建议 ──────────────────────────────────────────
    md += """## 💡 明日建议

"""
    if market_state in ('BEAR', 'CORRECTION'):
        md += "- 🔴 **防守优先**: 市场环境不利，严格控制仓位，关注止损\n"
    elif market_state == 'CRAB':
        md += "- 🟡 **中性观望**: 震荡市场，精选信号，分批建仓\n"
    else:
        md += "- 🟢 **积极布局**: 市场向好，关注 STRONG_BUY 信号\n"

    if top_buys:
        md += f"- 关注: {', '.join([s.get('symbol', '?') for s in top_buys[:3]])}\n"
    if sells:
        md += f"- 回避: {', '.join([s.get('symbol', '?') for s in sells])}\n"

    md += f"\n---\n*生成时间: {datetime.now().isoformat()}*\n"

    # ── 写入文件 ──────────────────────────────────────────
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(md)

    print(f'  [日报] 已生成: {report_file}')
    return str(report_file)