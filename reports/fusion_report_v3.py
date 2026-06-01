# -*- coding: utf-8 -*-
"""
fusion_report_v3.py — QuantBot 全量 FusionController 日报 v3.0

替代 reporter.py，解决以下问题:
  1. 全量展示(港股7只+美股15只全部)而非仅TOP 5
  2. 三因子完整分解: XMM/VP/LLM 原始输出 × 权重 = 加权贡献
  3. 每只标的的根因分析 (reasoning 展开)
  4. 与前一交易日的信号时间序列对比
  5. 执行层追踪 (哪些 BUY 被跳过及原因)
  6. 风控仪表板 (止损/冷却期/仓位限制)
  7. 市场状态驱动的仓位上限提示

用法:
  from reports.fusion_report_v3 import generate_v3_report
  report_path = generate_v3_report(
      date='2026-05-28', market='HK', signals=signals,
      orders=orders, positions=positions, account=account,
  )
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

BASE = Path('E:/quant')
REPORT_DIR = BASE / 'reports'
SIGNAL_DIR = BASE / 'paper_trading' / 'signals'

# ─── 常量 ────────────────────────────────────────────────────────
MARKET_LABELS = {
    'HK': '港股',
    'US': '美股',
}

SIGNAL_LEVEL_EMOJI = {
    'STRONG_BUY': '🟢🟢',
    'BUY': '🟢',
    'HOLD': '⬜',
    'REDUCED': '🟡',
    'SELL': '🔴',
    'STRONG_SELL': '🔴🔴',
    'BLOCKED': '🚫',
    'ERROR': '❌',
}

MARKET_STATE_LIMITS = {
    'BULL': 0.80,
    'RECOVERY': 0.60,
    'CRAB': 0.40,
    'CORRECTION': 0.20,
    'BEAR': 0.10,
}

MARKET_STATE_COLOR = {
    'BULL': '🟢',
    'RECOVERY': '🟢🟡',
    'CRAB': '🟡',
    'CORRECTION': '🔴🟡',
    'BEAR': '🔴',
}

HK_SYMBOLS = {
    '00700.HK': '腾讯',
    '09988.HK': '阿里巴巴',
    '03690.HK': '美团',
    '01024.HK': '快手',
    '01810.HK': '小米',
    '00981.HK': '中芯国际',
    '02513.HK': '智谱',
}

US_SYMBOLS = {
    'AAPL.US': 'Apple', 'AMZN.US': 'Amazon', 'MSFT.US': 'Microsoft',
    'GOOGL.US': 'Google', 'META.US': 'Meta', 'NVDA.US': 'Nvidia',
    'TSLA.US': 'Tesla', 'AMD.US': 'AMD', 'AVGO.US': 'Broadcom',
    'ORCL.US': 'Oracle', 'NFLX.US': 'Netflix', 'CRM.US': 'Salesforce',
    'ADBE.US': 'Adobe', 'INTC.US': 'Intel', 'QCOM.US': 'Qualcomm',
}

SYMBOL_MAP = {**HK_SYMBOLS, **US_SYMBOLS}


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

def generate_v3_report(date: str, market: str,
                       signals: list,
                       orders: list = None,
                       positions: list = None,
                       account: dict = None,
                       market_report: dict = None) -> str:
    """
    生成 v3.0 结构化日报。

    Args:
        date:       日期 'YYYY-MM-DD'
        market:     'HK' 或 'US'
        signals:    FusionController 分析结果列表 (来自 _fc_result_to_signal 映射)
        orders:     当日订单列表
        positions:  持仓列表
        account:    账户信息
        market_report: 市场状态报告

    Returns:
        report_path: 报告文件路径
    """
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    mkt_prefix = {'HK': 'hk', 'US': 'us'}.get(market, market.lower())
    report_path = REPORT_DIR / f'{date}_{mkt_prefix}_v3.md'

    orders = orders or []
    positions = positions or []

    # ─── 信号按等级分组 ────────────────────────────────────────
    strong_buys = [s for s in signals if s.get('fusion_level') == 'STRONG_BUY']
    buys = [s for s in signals if s.get('fusion_level') == 'BUY']
    holds = [s for s in signals if s.get('fusion_level') == 'HOLD']
    sells = [s for s in signals if s.get('fusion_level') in ('SELL', 'STRONG_SELL')]
    reduced = [s for s in signals if s.get('fusion_level') == 'REDUCED']
    errors = [s for s in signals if s.get('fusion_level') == 'ERROR']
    all_buy = strong_buys + buys

    # ─── 市场状态 ──────────────────────────────────────────────
    ms = market_report or {}
    market_state = ms.get('market_state', 'CRAB') if isinstance(ms, dict) else 'CRAB'
    vix_regime = ms.get('vix_regime', 'CANDIDATE') if isinstance(ms, dict) else 'CANDIDATE'
    trend = ms.get('trend', 'N/A') if isinstance(ms, dict) else 'N/A'
    momentum = ms.get('momentum', 'N/A') if isinstance(ms, dict) else 'N/A'
    state_pos_limit = MARKET_STATE_LIMITS.get(market_state, 0.40)

    # ─── 账户概览 ──────────────────────────────────────────────
    if account:
        total_assets = account.get('total_assets', 0)
        cash = account.get('cash', 0)
        pos_value = account.get('market_val', 0)
        n_positions = len(positions)
    else:
        total_assets = cash = pos_value = 0
        n_positions = len(positions)

    # ─── 前一期信号 (用于时间对比) ────────────────────────────
    prev_signals = _load_prev_signals(date, market)

    # ─── 生成报告正文 ──────────────────────────────────────────
    md = _build_header(date, market, market_state, vix_regime, trend, momentum, state_pos_limit)
    md += _build_account_section(total_assets, cash, pos_value, n_positions)
    md += _build_signal_summary(signals, all_buy, sells, reduced, holds, errors)
    md += _build_full_matrix(signals, prev_signals)
    md += _build_factor_decomposition(all_buy, reduced, sells)
    md += _build_delta_section(signals, prev_signals, date)
    md += _build_execution_tracking(orders, positions, signals, market)
    md += _build_risk_dashboard(positions, signals)
    md += _build_strategy_notes(signals, market_state, market)

    md += f"\n\n---\n*QuantBot FusionController v3.0 | 生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n"

    # ─── 写入 ──────────────────────────────────────────────────
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(md)

    print(f'  [v3日报] 已生成: {report_path}')
    return str(report_path)


# ═══════════════════════════════════════════════════════════════════
# 各节构建函数
# ═══════════════════════════════════════════════════════════════════

def _build_header(date, market, market_state, vix_regime, trend, momentum, pos_limit):
    mkt_label = MARKET_LABELS.get(market, market)
    state_icon = MARKET_STATE_COLOR.get(market_state, '⬜')
    n_stocks = 7 if market == 'HK' else 15

    return f"""# QuantBot FusionController 日报 v3.0

> {date} | {mkt_label} ({n_stocks} 标的) | 状态: {state_icon} {market_state} | VIX: {vix_regime}

## I. 市场环境

| 维度 | 状态 | 含义 |
|------|------|------|
| **市场状态** | {state_icon} **{market_state}** | 仓位上限 {pos_limit:.0%} |
| **VIX 环境** | {vix_regime} | {'压制买盘(>35)' if vix_regime == 'SUPPRESSED' else '正常' if vix_regime == 'CANDIDATE' else '释放卖压(<22)'} |
| **趋势方向** | {trend} | — |
| **动量** | {momentum} | — |
"""


def _build_account_section(total_assets, cash, pos_value, n_positions):
    if total_assets <= 0:
        return ""

    cash_pct = f'{cash / total_assets:.1%}' if total_assets > 0 else 'N/A'
    pos_pct = f'{pos_value / total_assets:.1%}' if total_assets > 0 else 'N/A'

    return f"""## II. 账户概览

| 指标 | 数值 |
|------|------|
| **总资产** | {total_assets:,.0f} |
| **现金** | {cash:,.0f} ({cash_pct}) |
| **持仓市值** | {pos_value:,.0f} ({pos_pct}) |
| **持仓数** | {n_positions} |
"""


def _build_signal_summary(signals, all_buy, sells, reduced, holds, errors):
    total = len(signals)
    if total == 0:
        return ""

    s = f"""## III. 信号分布总览

| 等级 | 数量 | 占比 |
|------|------|------|
| 🟢🟢 STRONG_BUY | {len([s for s in all_buy if s.get('fusion_level')=='STRONG_BUY'])} | {len([s for s in all_buy if s.get('fusion_level')=='STRONG_BUY'])/total:.0%} |
| 🟢 BUY | {len([s for s in all_buy if s.get('fusion_level')=='BUY'])} | {len([s for s in all_buy if s.get('fusion_level')=='BUY'])/total:.0%} |
| 🟡 REDUCED | {len(reduced)} | {len(reduced)/total:.0%} |
| ⬜ HOLD | {len(holds)} | {len(holds)/total:.0%} |
| 🔴 SELL | {len(sells)} | {len(sells)/total:.0%} |
"""

    if errors:
        s += f"| ❌ ERROR | {len(errors)} | {len(errors)/total:.0%} |\n"

    # 分数分布
    scores = [sig.get('fusion_score', 0) for sig in signals if sig.get('fusion_level') != 'ERROR']
    if scores:
        avg_score = sum(scores) / len(scores)
        pos_scores = [s for s in scores if s > 0]
        neg_scores = [s for s in scores if s < 0]
        s += f"""
**分数统计**: 均值 {avg_score:+.1f} | 正值 {len(pos_scores)}只 | 负值 {len(neg_scores)}只 | 范围 [{min(scores):+.1f}, {max(scores):+.1f}]
"""
    s += "\n"
    return s


def _build_full_matrix(signals, prev_signals):
    """全量信号矩阵 - 每只标的的完整 FusionController 输出."""
    if not signals:
        return ""

    prev_map = {p['symbol']: p for p in prev_signals}

    s = f"""## IV. 全量信号矩阵 ({len(signals)} 标的)

| # | 标的 | 现价 | RSI日 | RSI周 | 信号 | 融合分 | 置信度 | XMM分 | VP分 | LLM分 | 权重 | 仓位 | 变动 | 根因 |
|---|------|------|-------|-------|------|--------|--------|-------|------|-------|------|------|------|------|
"""

    sorted_signals = sorted(signals, key=lambda x: x.get('fusion_score', 0), reverse=True)

    for i, sig in enumerate(sorted_signals, 1):
        sym = sig.get('symbol', '?')
        name = SYMBOL_MAP.get(sym, '')
        display_name = f"{sym}" if not name else f"{sym}<br><sub>{name}</sub>"
        close = sig.get('close', 0)
        rsi_d = sig.get('rsi_daily', 50)
        rsi_w = sig.get('rsi_weekly', 50)
        level = sig.get('fusion_level', '?')
        score = sig.get('fusion_score', 0)
        conf = sig.get('fusion_confidence', 0)
        target_pos = sig.get('target_position', 0)
        weights = sig.get('weights_used', {})
        raw = sig.get('raw_scores', {})
        xmm_raw = raw.get('xmm', 0)
        vp_raw = raw.get('vp', 0)
        llm_raw = raw.get('llm', 0)

        icon = SIGNAL_LEVEL_EMOJI.get(level, '⬜')

        # 权重显示
        wx = weights.get('xmm', 0)
        wv = weights.get('vp', 0)
        wl = weights.get('llm', 0)
        weight_str = f"X:{wx:.0%}/V:{wv:.0%}/L:{wl:.0%}"

        # 与前次对比
        prev = prev_map.get(sym)
        delta_str = ''
        if prev and prev.get('fusion_level') != 'ERROR':
            prev_score = prev.get('fusion_score', 0)
            prev_level = prev.get('fusion_level', '?')
            delta = score - prev_score
            if abs(delta) >= 5:
                delta_str = f"{delta:+.0f}" + ('↑' if delta > 0 else '↓')
            if prev_level != level:
                delta_str += f" {prev_level}→{level}"

        # 根因分析
        root_cause = _extract_root_cause(sig)

        rsi_d_icon = _rsi_color(rsi_d)
        rsi_w_icon = _rsi_color(rsi_w)

        s += f"| {i} | {display_name} | {close:.2f} | {rsi_d_icon}{rsi_d:.0f} | {rsi_w_icon}{rsi_w:.0f} | {icon}**{level}** | **{score:+.1f}** | {conf:.0%} | {xmm_raw:+.0f} | {vp_raw:+.0f} | {llm_raw:+.0f} | {weight_str} | {target_pos:.0%} | {delta_str} | {root_cause} |\n"

    s += "\n"
    return s


def _build_factor_decomposition(all_buy, reduced, sells):
    """三因子分解详情 — 展示 XMM/VP/LLM 各因子的原始输出和加权贡献."""
    if not all_buy and not reduced and not sells:
        return ""

    s = "## V. 三因子加权分解\n\n"

    for group_name, group_signals in [
        ('BUY 信号', all_buy),
        ('REDUCED 信号', reduced),
        ('SELL 信号', sells),
    ]:
        if not group_signals:
            continue

        s += f"### {group_name} ({len(group_signals)} 只)\n\n"
        s += "| 标的 | 融合分 | XMM原始 | →加权 | VP原始 | →加权 | LLM原始 | →加权 | XMM详情 | VP详情 | LLM详情 |\n"
        s += "|------|--------|---------|-------|--------|-------|---------|-------|----------|--------|----------|\n"

        for sig in group_signals:
            sym = sig.get('symbol', '?')
            score = sig.get('fusion_score', 0)
            weights = sig.get('weights_used', {})
            raw = sig.get('raw_scores', {})
            xmm_raw = raw.get('xmm', 0)
            vp_raw = raw.get('vp', 0)
            llm_raw = raw.get('llm', 0)
            wx = weights.get('xmm', 0)
            wv = weights.get('vp', 0)
            wl = weights.get('llm', 0)

            # 加权贡献 = raw × weight (这里raw是已经映射到[-100,100]的分数，直接乘权重)
            xmm_contrib = xmm_raw * wx
            vp_contrib = vp_raw * wv
            llm_contrib = llm_raw * wl

            # 详情
            xmm_detail = _get_xmm_detail(sig)
            vp_detail = _get_vp_detail(sig)
            llm_detail = _get_llm_detail(sig)

            s += f"| {sym} | **{score:+.1f}** | {xmm_raw:+.0f} | {xmm_contrib:+.1f} | {vp_raw:+.0f} | {vp_contrib:+.1f} | {llm_raw:+.0f} | {llm_contrib:+.1f} | {xmm_detail} | {vp_detail} | {llm_detail} |\n"

        s += "\n"

    # 权重详解
    s += """### 权重体系

| 因子 | 权重 | 角色 | 数据源 |
|------|------|------|--------|
| **XMM** (徐小明策略) | 60% | 主力方向判断 | 双EMA趋势 + MACD结构 + TD9序列 |
| **VP** (Volume Profile) | 25% | 筹码箱体确认 | 成交量分布 + VAH/VAL/POC |
| **LLM** (AI市场感知) | 15% | 情绪/事件侦察 | NVIDIA NIM / 事件分析 |

> 异常源断流时权重自动归零，存活源按比例重新归一化。

"""
    return s


def _build_delta_section(signals, prev_signals, date):
    """与前次信号的时间序列对比."""
    if not prev_signals:
        return ""

    prev_map = {p['symbol']: p for p in prev_signals}
    prev_date = prev_signals[0].get('date', '前次') if prev_signals else '前次'

    changes = []
    for sig in signals:
        sym = sig.get('symbol', '?')
        prev = prev_map.get(sym)
        if not prev:
            continue
        delta = sig.get('fusion_score', 0) - prev.get('fusion_score', 0)
        level_change = sig.get('fusion_level') != prev.get('fusion_level')
        if abs(delta) >= 5 or level_change:
            changes.append({
                'symbol': sym,
                'prev_score': prev.get('fusion_score', 0),
                'curr_score': sig.get('fusion_score', 0),
                'delta': delta,
                'prev_level': prev.get('fusion_level', '?'),
                'curr_level': sig.get('fusion_level', '?'),
            })

    if not changes:
        return ""

    s = f"""## VI. 信号变动对比 (vs {prev_date})

> 仅列出融合分变动 ≥5 或信号等级发生变化的标的

| 标的 | 前次分 | 当前分 | Δ | 前次等级 | 当前等级 |
|------|--------|--------|-----|----------|----------|
"""

    changes.sort(key=lambda x: abs(x['delta']), reverse=True)
    for c in changes:
        s += f"| {c['symbol']} | {c['prev_score']:+.1f} | {c['curr_score']:+.1f} | {c['delta']:+.0f}{'↑' if c['delta']>0 else '↓'} | {c['prev_level']} | {c['curr_level']} |\n"

    s += "\n"
    return s


def _build_execution_tracking(orders, positions, signals, market):
    """执行层追踪 — 哪些信号被执行，哪些被跳过."""
    s = "## VII. 执行追踪\n\n"

    # 今日订单
    buy_orders = [o for o in orders if o.get('action') == 'BUY']
    sell_orders = [o for o in orders if o.get('action') in ('SELL', 'STOP', 'SIGNAL_EXIT')]
    skip_reasons = []

    for o in orders:
        if o.get('reason', '').startswith('[SKIP]'):
            skip_reasons.append(o)

    if buy_orders:
        s += "### 买入执行\n\n"
        s += "| 标的 | 数量 | 价格 | 金额 | 原因 |\n"
        s += "|------|------|------|------|------|\n"
        for o in buy_orders:
            cost = o.get('qty', 0) * o.get('price', 0)
            s += f"| {o.get('symbol', '?')} | {o.get('qty', 0)} | {o.get('price', 0):.2f} | {cost:,.0f} | {o.get('reason', '')[:50]} |\n"

    if sell_orders:
        s += "\n### 卖出执行\n\n"
        s += "| 标的 | 数量 | 价格 | 原因 |\n"
        s += "|------|------|------|------|\n"
        for o in sell_orders:
            s += f"| {o.get('symbol', '?')} | {o.get('qty', 0)} | {o.get('price', 0):.2f} | {o.get('reason', '')[:50]} |\n"

    # BUY 信号未执行的原因
    signal_map = {s['symbol']: s for s in signals}
    held_syms = {p.get('symbol', '') for p in positions}
    buy_signals = [s for s in signals if s.get('fusion_level') in ('STRONG_BUY', 'BUY')]
    unexecuted = [s for s in buy_signals if s['symbol'] not in {o.get('symbol', '') for o in buy_orders}]

    if unexecuted:
        s += "\n### BUY 信号未执行分析\n\n"
        s += "| 标的 | 信号 | 分数 | 置信度 | 跳过原因 |\n"
        s += "|------|------|------|--------|----------|\n"
        for sig in unexecuted:
            sym = sig['symbol']
            reason = ''
            if sym in held_syms:
                reason = '已持有'
            elif sig.get('fusion_confidence', 0) < 0.65:
                reason = f'置信度不足 ({sig["fusion_confidence"]:.0%} < 65%)'
            elif sig.get('fusion_score', 0) < 40:
                reason = f'分数不足 ({sig["fusion_score"]:+.0f} < +40)'
            else:
                reason = '仓位/预算限制'
            s += f"| {sym} | {sig['fusion_level']} | {sig['fusion_score']:+.0f} | {sig.get('fusion_confidence', 0):.0%} | {reason} |\n"

    if not buy_orders and not sell_orders and not unexecuted:
        s += "本时段无执行记录。\n"

    s += "\n"
    return s


def _build_risk_dashboard(positions, signals):
    """风控仪表板."""
    s = "## VIII. 风控仪表板\n\n"

    if positions:
        s += "### 持仓状态\n\n"
        s += "| 标的 | 股数 | 成本价 | 现价 | PnL% | 信号 | 风控状态 |\n"
        s += "|------|------|--------|------|------|------|----------|\n"
        sig_map = {s['symbol']: s for s in signals}
        for p in positions:
            sym = p.get('symbol', '?')
            qty = p.get('qty', p.get('shares', 0))
            entry = p.get('entry_price', p.get('cost_price', 0))
            cur = p.get('current_price', 0)
            pnl = (cur / entry - 1) * 100 if entry > 0 else 0
            sig = sig_map.get(sym, {})
            curr_level = sig.get('fusion_level', 'N/A')
            risk_color = '🟢' if pnl >= 0 else ('🟡' if pnl > -5 else '🔴')
            triggered = '触发止损' if p.get('triggered_stop') else '正常'
            s += f"| {sym} | {qty} | {entry:.2f} | {cur:.2f} | {risk_color} {pnl:+.2f}% | {curr_level} | {triggered} |\n"
    else:
        s += "当前无持仓。\n"

    s += "\n### 风控参数\n\n"
    s += "| 参数 | 设定值 | 说明 |\n"
    s += "|------|--------|------|\n"
    s += "| 单只最大仓位 | 20% | 超额自动跳过 |\n"
    s += "| 总仓位上限 | 80% | 硬门槛 |\n"
    s += "| 固定止损 | -12% | 无条件触发 |\n"
    s += "| 移动止损 | -12% | 高点回撤 |\n"
    s += "| 组合回撤 | -30% | 全局熔断 |\n"
    s += "| 止损冷却期 | 10天 | 同标的间隔 |\n"
    s += "| 分批建仓 | 3批 (34%/33%/33%) | 首批浮亏≤2%才加仓 |\n"
    s += "\n"

    return s


def _build_strategy_notes(signals, market_state, market):
    """策略备注 — 关键观察."""
    s = "## IX. 策略备注\n\n"

    # 统计异常
    all_buy = [sig for sig in signals if sig.get('fusion_level') in ('STRONG_BUY', 'BUY')]
    reduced = [sig for sig in signals if sig.get('fusion_level') == 'REDUCED']
    xmm_zeros = [sig for sig in signals if sig.get('raw_scores', {}).get('xmm', 0) == 0]

    observations = []

    # XMM 零信号分析
    if len(xmm_zeros) > len(signals) * 0.5:
        observations.append(f"XMM 因子大面积中性（{len(xmm_zeros)}/{len(signals)} 标的 XMM=0）→ 趋势不明朗，VP 和 LLM 无法独自驱动 BUY 信号（因 XMM 占 60% 权重）")

    # REDUCED 比例分析
    if reduced:
        reduced_pct = len(reduced) / len(signals) if signals else 0
        if reduced_pct > 0.3:
            # 判断 REDUCED 总体方向
            reduced_scores = [sig.get('fusion_score', 0) for sig in reduced]
            reduced_avg = sum(reduced_scores) / len(reduced_scores) if reduced_scores else 0
            if reduced_avg > 0:
                observations.append(f"REDUCED 占比偏高 ({reduced_pct:.0%}) → 多数标的处于 BUY 阈值边缘，说明有一定正向信号但强度不足")
            else:
                observations.append(f"REDUCED 占比偏高 ({reduced_pct:.0%}) → 多数标的处于 SELL 阈值边缘（负分），信号偏空")

        # REDUCED 根因分析
        reduced_xmm_zero = [sig for sig in reduced if sig.get('raw_scores', {}).get('xmm', 0) == 0]
        if reduced_xmm_zero:
            vp_llm_signs = []
            for ss in reduced_xmm_zero:
                raw = ss.get('raw_scores', {})
                if raw.get('vp', 0) > 0 and raw.get('llm', 0) > 0:
                    vp_llm_signs.append('both_bull')
                elif raw.get('vp', 0) < 0 and raw.get('llm', 0) < 0:
                    vp_llm_signs.append('both_bear')
                else:
                    vp_llm_signs.append('mixed')
            if vp_llm_signs.count('both_bull') > len(vp_llm_signs) / 2:
                observations.append(f"{len(reduced_xmm_zero)}/{len(reduced)} REDUCED 由 XMM=0 导致 → VP 和 LLM 偏多但被 XMM 拉低融合分至 [10,30) 区间")
            else:
                observations.append(f"{len(reduced_xmm_zero)}/{len(reduced)} REDUCED 由 XMM=0 导致 → VP/LLM 偏空，融合分为负但未达 SELL 阈值 (-30)")

    # 市场状态影响
    state_limit = MARKET_STATE_LIMITS.get(market_state, 0.40)
    if market_state in ('BEAR', 'CORRECTION'):
        observations.append(f"市场状态 {market_state} → 仓位硬上限 {state_limit:.0%}，大幅压制建仓能力")
    elif market_state == 'CRAB':
        observations.append(f"市场状态 CRAB → 仓位上限 {state_limit:.0%}，震荡市宜精选信号分批建仓")
    else:
        observations.append(f"市场状态 {market_state} → 仓位上限 {state_limit:.0%}，可积极布局")

    # 数据新鲜度
    stale = [s for s in signals if s.get('warnings') and any('滞后' in w for w in s.get('warnings', []))]
    if stale:
        stale_names = ', '.join(s['symbol'] for s in stale[:3])
        observations.append(f"数据滞后: {len(stale)} 只标的（{stale_names}...）→ 信号置信度已按公式 decay=max(0.5, 1.0-stale_days×0.02) 衰减")

    if observations:
        for i, obs in enumerate(observations, 1):
            s += f"{i}. {obs}\n"
    else:
        s += "无特殊备注。\n"

    s += "\n"
    return s


# ═══════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════

def _load_prev_signals(date: str, market: str) -> list:
    """加载前一交易日的信号数据。"""
    try:
        target_date = datetime.strptime(date, '%Y-%m-%d')
        # 往前找最多 7 天
        for i in range(1, 8):
            prev_date = (target_date - timedelta(days=i)).strftime('%Y-%m-%d')
            sig_file = SIGNAL_DIR / f'{prev_date}_{market}.json'
            if sig_file.exists():
                with open(sig_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get('signals', [])
    except Exception:
        pass
    return []


def _extract_root_cause(sig: dict) -> str:
    """提取信号的根因诊断（v3 增强：含因子源状态）。"""
    level = sig.get('fusion_level', '')
    score = sig.get('fusion_score', 0)
    raw = sig.get('raw_scores', {})
    xmm_raw = raw.get('xmm', 0)
    vp_raw = raw.get('vp', 0)
    llm_raw = raw.get('llm', 0)
    reasoning = sig.get('reasoning', '')
    warnings = sig.get('warnings', [])
    xmm_status = sig.get('xmm_status', '')
    vp_status = sig.get('vp_status', '')
    llm_status = sig.get('llm_status', '')
    stale = sig.get('stale_days', 0)
    gate_approved = sig.get('gate_approved', True)
    gate_reasons = sig.get('gate_reasons', [])

    parts = []

    # 数据过期
    if stale > 7:
        parts.append(f'数据过期{stale}天')

    # HardGate 拦截 — 精简标记，详细原因在 warnings
    if not gate_approved and gate_reasons:
        parts.append('Gate拦截')

    # 源异常
    if xmm_status not in ('OK', ''):
        parts.append(f'XMM异常[{xmm_status}]')
    if vp_status not in ('OK', ''):
        parts.append(f'VP异常[{vp_status}]')
    if llm_status not in ('OK', ''):
        parts.append(f'LLM异常[{llm_status}]')

    # ERROR
    if level == 'ERROR':
        return ' | '.join(parts) if parts else '数据/策略异常'

    # STRONG_BUY / BUY: 哪个因子做主要贡献
    if level in ('STRONG_BUY', 'BUY'):
        contributions = [('XMM', xmm_raw), ('VP', vp_raw), ('LLM', llm_raw)]
        contributions.sort(key=lambda x: x[1], reverse=True)
        top = contributions[0]
        second = contributions[1] if len(contributions) > 1 else None
        if top[1] > 30:
            parts.append(f'{top[0]}主力(+{top[1]:.0f})')
        elif second and second[1] > 30:
            parts.append(f'{second[0]}主力(+{second[1]:.0f})')
        else:
            parts.append(f'多因子共振(+{score:+.0f})')

    # HOLD: 解释为什么没有信号
    elif level == 'HOLD':
        if xmm_raw == 0 and vp_raw > 0:
            parts.append('XMM中性->压分; VP偏多但权重(25%)不足以触发')
        elif xmm_raw == 0 and vp_raw < 0:
            parts.append('XMM中性; VP偏空->压分')
        elif xmm_raw == 0 and vp_raw == 0:
            parts.append('三因子均中性(XMM=VP=0)')
        elif abs(xmm_raw) < 10 and abs(vp_raw) < 10 and abs(llm_raw) < 10:
            parts.append('三因子均中性(-10~+10)')
        elif abs(llm_raw) < 10 and abs(vp_raw) < 10:
            parts.append(f'仅XMM微弱信号({xmm_raw:+.0f}),VP/LLM中性')
        elif reasoning:
            short = reasoning[:30]
            if len(short) > 2:
                parts.append(short)
        else:
            parts.append('无明确信号')

    # REDUCED: 为什么在 BUY 边缘但未突破
    elif level == 'REDUCED':
        if xmm_raw == 0:
            # XMM=0 压低了融合分 — 根据 VP+LLM 的实际方向判断
            vp_llm_combined = vp_raw * 0.25 + llm_raw * 0.15
            direction = '偏多' if vp_llm_combined > 0 else '偏空'
            max_possible = (vp_raw * 0.25 + llm_raw * 0.15) / 0.40
            parts.append(f'XMM=0 压分(上限{max_possible:.0f}); VP+LLM{direction}')
        elif xmm_raw > 0 and vp_raw < 0:
            parts.append(f'XMM偏多(+{xmm_raw:.0f}) 但 VP偏空({vp_raw:.0f}) 抵消')
        elif xmm_raw < 0 and vp_raw > 0:
            parts.append(f'VP偏多(+{vp_raw:.0f}) 但 XMM偏空({xmm_raw:.0f}) 抵消')
        elif xmm_raw < 0 and vp_raw < 0:
            # 两因子同时偏空但未达 SELL 阈值
            parts.append(f'XMM偏空({xmm_raw:.0f})+VP偏空({vp_raw:.0f}); 未达SELL')
        elif abs(llm_raw) > 50:
            parts.append(f'LLM极端({llm_raw:+.0f})->信号偏弱')
        elif reasoning:
            short = reasoning[:35]
            if short:
                parts.append(short)

    # SELL / STRONG_SELL
    elif level in ('SELL', 'STRONG_SELL'):
        contributions = [('XMM', xmm_raw), ('VP', vp_raw), ('LLM', llm_raw)]
        contributions.sort(key=lambda x: x[1])
        worst = contributions[0]
        if worst[1] < -30:
            parts.append(f'{worst[0]}驱动({worst[1]:+.0f})')
        else:
            parts.append(f'综合偏空({score:+.0f})')

    # 额外警告 (排除 Gate 拦截相关，已在上面处理)
    gate_keywords = ['置信度', '融合得分', '等级:', 'Gate']
    for w in warnings:
        short_w = w[:25].replace('|', ' ')
        # 跳过 Gate 相关警告
        if any(kw in short_w for kw in gate_keywords):
            continue
        if short_w not in ' '.join(parts):
            parts.append(short_w)
            if len(parts) >= 3:
                break

    return ' | '.join(parts) if parts else '—'


def _get_xmm_detail(sig: dict) -> str:
    """提取 XMM 因子详细状态（v3 增强：直接使用源数据）。"""
    reasoning = sig.get('reasoning', '')
    xmm_action = sig.get('xmm_action', 'HOLD')
    xmm_reason = sig.get('xmm_reason', '')
    xmm_trend = sig.get('xmm_trend', 'UNKNOWN')
    xmm_td = sig.get('xmm_td_count', 0)
    xmm_status = sig.get('xmm_status', 'SKIPPED')
    raw = sig.get('raw_scores', {})
    xmm_raw = raw.get('xmm', 0)

    if xmm_status != 'OK':
        return f'[{xmm_status}]'

    parts = []
    parts.append(xmm_action)
    if xmm_trend and xmm_trend != 'UNKNOWN':
        parts.append(f'趋势:{xmm_trend}')
    if xmm_reason:
        short_reason = xmm_reason[:20].replace('|', ' ')
        parts.append(short_reason)
    if xmm_td:
        parts.append(f'TD{xmm_td}')

    detail = ' '.join(parts) if parts else 'HOLD(中性)'
    if len(detail) > 30:
        detail = detail[:28] + '..'
    return detail


def _get_vp_detail(sig: dict) -> str:
    """提取 VP 因子详细状态（v3 增强：含 VAH/VAL/POC）。"""
    reasoning = sig.get('reasoning', '')
    vp_state = sig.get('vp_state', 'unknown')
    vp_vah = sig.get('vp_vah', 0)
    vp_val = sig.get('vp_val', 0)
    vp_poc = sig.get('vp_poc', 0)
    vp_status = sig.get('vp_status', 'SKIPPED')
    raw = sig.get('raw_scores', {})
    vp_raw = raw.get('vp', 0)

    if vp_status != 'OK':
        return f'[{vp_status}]'

    state_map = {
        'above_box': '上轨外↑',
        'below_box': '下轨外↓',
        'inside_box': '箱体内',
    }
    state_label = state_map.get(vp_state, vp_state)

    if vp_vah and vp_val:
        return f'{state_label} [POC:{vp_poc:.1f}]' if vp_poc else state_label
    elif vp_raw == 0:
        return '箱体内(中性)'
    elif vp_raw > 0:
        return f'偏多({vp_raw:+.0f})'
    else:
        return f'偏空({vp_raw:+.0f})'


def _get_llm_detail(sig: dict) -> str:
    """提取 LLM 因子详细状态（v3 增强：含事件摘要）。"""
    reasoning = sig.get('reasoning', '')
    llm_sentiment = sig.get('llm_sentiment', 0)
    llm_summary = sig.get('llm_summary', '')
    llm_event = sig.get('llm_event_type', '')
    llm_status = sig.get('llm_status', 'SKIPPED')
    raw = sig.get('raw_scores', {})
    llm_raw = raw.get('llm', 0)

    if llm_status != 'OK':
        return f'[{llm_status}]'

    if llm_summary:
        brief = llm_summary[:20].replace('|', ' ')
        return f'{brief}'
    elif llm_event:
        return f'{llm_event}({llm_raw:+.0f})'
    elif llm_raw == 0:
        return '中性'
    else:
        return f'偏向{llm_raw:+.0f}'


def _rsi_color(value: float) -> str:
    """RSI 颜色标记。"""
    if value >= 70:
        return '🔥'
    elif value <= 30:
        return '❄️'
    return ''


# ═══════════════════════════════════════════════════════════════════
# CLI 入口（独立测试用）
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    """独立使用: python fusion_report_v3.py <date> <market>"""
    import argparse

    parser = argparse.ArgumentParser(description='QuantBot FusionController v3 日报生成器')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--market', type=str, required=True, choices=['HK', 'US'])
    args = parser.parse_args()

    # 从信号 JSON 文件加载数据
    sig_file = SIGNAL_DIR / f'{args.date}_{args.market}.json'
    if not sig_file.exists():
        print(f'[ERROR] 信号文件不存在: {sig_file}')
        sys.exit(1)

    with open(sig_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    signals = data.get('signals', [])
    orders = data.get('orders', [])

    report_path = generate_v3_report(
        date=args.date,
        market=args.market,
        signals=signals,
        orders=orders,
    )

    print(f'\nDone: {report_path}')
