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
import re
from pathlib import Path
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from core.paths import PROJECT_ROOT
BASE = PROJECT_ROOT
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
                       market_report: dict = None,
                       execution_mode: str = '',
                       execution_results: list = None,
                       report_mode: str = '',
                       account_access: bool = None,
                       order_api_called: bool = None) -> str:
    """
    生成 v3.0 结构化日报。

    Args:
        date:       日期 'YYYY-MM-DD'
        market:     'HK' 或 'US'
        signals:    FusionController 分析结果列表 (来自 _fc_result_to_signal 映射)
        orders:     当日候选订单列表（不代表已成交）
        positions:  持仓列表
        account:    账户信息
        market_report: 市场状态报告
        execution_mode: SIGNAL_ONLY/DRY_RUN/LIVE_CONFIRMED/LIVE_BLOCKED_*
        execution_results: OrderExecutor 返回的实际执行或模拟结果
        report_mode: RESEARCH_ONLY_COMPLETED_DAILY 或其他报告模式
        account_access: 本次报告流程是否访问账户能力
        order_api_called: 本次报告流程是否调用订单 API

    Returns:
        report_path: 报告文件路径
    """
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    mkt_prefix = {'HK': 'hk', 'US': 'us'}.get(market, market.lower())
    report_path = REPORT_DIR / f'{date}_{mkt_prefix}_v3.md'

    orders = orders or []
    positions = positions or []
    execution_results = execution_results or []

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
    generated_at = datetime.now().astimezone()
    md = _build_header(
        date, market, market_state, vix_regime, trend, momentum,
        state_pos_limit, ms, signals=signals, generated_at=generated_at,
        report_mode=report_mode,
    )
    md += _build_account_section(
        total_assets, cash, pos_value, n_positions,
        report_mode=report_mode,
        account_access=account_access,
        order_api_called=order_api_called,
    )
    md += _build_signal_summary(signals, all_buy, sells, reduced, holds, errors)
    md += _build_factor_view_section(signals)
    md += _build_full_matrix(signals, prev_signals)
    md += _build_factor_decomposition(all_buy, reduced, sells)
    md += _build_delta_section(signals, prev_signals, date)
    md += _build_execution_tracking(
        orders, positions, signals, market,
        execution_mode=execution_mode,
        execution_results=execution_results,
        report_mode=report_mode,
        account_access=account_access,
        order_api_called=order_api_called,
    )
    md += _build_risk_dashboard(positions, signals, total_assets)
    md += _build_strategy_notes(signals, market_state, market)

    md += (
        "\n\n---\n"
        f"*QuantBot FusionController v3.0 | 生成: "
        f"{generated_at.isoformat(timespec='seconds')}*\n"
    )

    # ─── 写入 ──────────────────────────────────────────────────
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(md)

    print(f'  [v3日报] 已生成: {report_path}')
    return str(report_path)


# ═══════════════════════════════════════════════════════════════════
# 各节构建函数
# ═══════════════════════════════════════════════════════════════════

def _signal_data_cutoff(signals):
    """Return the declared signal date range without inventing bar finality."""
    signal_dates = sorted({
        str(signal.get('date')).strip()
        for signal in (signals or [])
        if signal.get('date')
    })
    if not signal_dates:
        return 'N/A'
    if len(signal_dates) == 1:
        return signal_dates[0]
    return f'{signal_dates[0]}..{signal_dates[-1]}'


def _signal_bar_finality(signals, market_report=None):
    """Use only explicitly declared finality; never infer it from wall time."""
    ms = market_report if isinstance(market_report, dict) else {}
    declared = []
    for value in (
        ms.get('bar_finality'),
        ms.get('data_finality'),
        ms.get('session_finality'),
    ):
        if value:
            declared.append(str(value).upper())
    for signal in signals or []:
        for key in ('bar_finality', 'data_finality', 'session_finality'):
            value = signal.get(key)
            if value:
                declared.append(str(value).upper())
    values = sorted(set(declared))
    if not values:
        return 'UNVERIFIED'
    return values[0] if len(values) == 1 else f"MIXED({','.join(values)})"


def _build_header(date, market, market_state, vix_regime, trend, momentum,
                  pos_limit, market_report=None, signals=None,
                  generated_at=None, report_mode=''):
    mkt_label = MARKET_LABELS.get(market, market)
    state_icon = MARKET_STATE_COLOR.get(market_state, '⬜')
    n_stocks = 7 if market == 'HK' else 15
    ms = market_report if isinstance(market_report, dict) else {}
    vxx_detail = ms.get('vix_detail', {})
    if not isinstance(vxx_detail, dict):
        vxx_detail = {}
    vxx_source = ms.get('vxx_source') or vxx_detail.get('source') or 'UNKNOWN'
    vxx_as_of = ms.get('vxx_as_of') or vxx_detail.get('as_of') or 'N/A'
    vxx_freshness = ms.get('vxx_freshness') or vxx_detail.get('freshness') or 'UNKNOWN'
    vxx_price = vxx_detail.get('vxx_price')
    vxx_price_text = f'{vxx_price:.2f}' if isinstance(vxx_price, (int, float)) else 'N/A'
    signal_cutoff = _signal_data_cutoff(signals)
    bar_finality = _signal_bar_finality(signals, ms)
    report_mode_text = report_mode or 'STANDARD'
    report_time = generated_at or datetime.now().astimezone()
    if report_time.tzinfo is None:
        report_time = report_time.astimezone()
    comparison_note = (
        "\n> 数据可比性：当前 QuantBot schema 未显式声明日线是否完成；"
        "与 `COMPLETED_SESSION_ONLY` 模型比较时必须标记 `NOT_COMPARABLE`。\n"
        if bar_finality == 'UNVERIFIED'
        else ''
    )

    return f"""# QuantBot FusionController 日报 v3.0

> {date} | {mkt_label} ({n_stocks} 标的) | 状态: {state_icon} {market_state} | VXX: {vix_regime}

## I. 市场环境

| 维度 | 状态 | 含义 |
|------|------|------|
| **市场状态** | {state_icon} **{market_state}** | 仓位上限 {pos_limit:.0%} |
| **VXX 环境** | {vix_regime} | {'压制买盘(>35)' if vix_regime == 'SUPPRESSED' else '正常' if vix_regime == 'CANDIDATE' else '释放卖压(<22)'} |
| **VXX 数据** | {vxx_price_text} · {vxx_freshness} | {vxx_source} · as-of {vxx_as_of} |
| **趋势方向** | {trend} | — |
| **动量** | {momentum} | — |
| **报告模式** | {report_mode_text} | 研究/执行边界显式声明 |
| **信号数据截止** | {signal_cutoff} | bar finality: {bar_finality} |
| **报告生成时间** | {report_time.isoformat(timespec='seconds')} | 含本地 UTC offset |
""" + comparison_note


def _build_account_section(total_assets, cash, pos_value, n_positions,
                           report_mode='', account_access=None,
                           order_api_called=None):
    if report_mode == 'RESEARCH_ONLY_COMPLETED_DAILY':
        return f"""## II. 研究边界

| 能力 | 状态 |
|------|------|
| **Account Access** | {'ENABLED' if account_access else 'DISABLED'} |
| **Order API Called** | {'YES' if order_api_called else 'NO'} |
| **订单与成交** | 研究模式不生成、不执行 |

"""
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

    s = f"""## V. 全量信号矩阵 ({len(signals)} 标的)

> conf_v² / Δconf 为 shadow 指标，仅用于观察置信度 v2，不参与 Gate、不影响仓位、不替换 live 置信度。

| # | 标的 | 信号价 | RSI日 | RSI周 | 信号 | 融合分 | 置信度 | conf_v² | Δconf | 三因子观点 | XMM分 | VP分 | LLM分 | 权重 | 目标仓位 | 有效仓位 | 变动 | 根因 |
|---|------|--------|-------|-------|------|--------|--------|---------|-------|------------|-------|------|-------|------|----------|----------|------|------|
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
        conf_v2, delta_conf = _compute_conf_v2_shadow(sig)
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
        root_cause = _safe_cell_text(_extract_root_cause(sig), 80)
        factor_view = _factor_view_summary(sig)

        rsi_d_icon = _rsi_color(rsi_d)
        rsi_w_icon = _rsi_color(rsi_w)

        conf_v2_str = f'{conf_v2:.0%}' if conf_v2 >= 0 else 'N/A'
        delta_conf_str = f'{delta_conf:+.0%}' if conf_v2 >= 0 else ''
        effective_pos = _effective_position_cell(sig)

        s += f"| {i} | {display_name} | {close:.2f} | {rsi_d_icon}{rsi_d:.0f} | {rsi_w_icon}{rsi_w:.0f} | {icon}**{level}** | **{score:+.1f}** | {conf:.0%} | {conf_v2_str} | {delta_conf_str} | {factor_view} | {xmm_raw:+.0f} | {vp_raw:+.0f} | {llm_raw:+.0f} | {weight_str} | {target_pos:.0%} | {effective_pos} | {delta_str} | {root_cause} |\n"

    s += "\n"
    return s


def _build_factor_view_section(signals):
    """展示有效 alpha 因子观点，并将 audit-only LLM 单独标注。"""
    if not signals:
        return ""

    sorted_signals = sorted(signals, key=lambda x: x.get('fusion_score', 0), reverse=True)

    audit_only = any(_llm_is_audit_only(sig) for sig in signals)
    title = '有效因子观点 + LLM审计' if audit_only else '三因子直接观点'
    consensus_title = '有效因子一致性' if audit_only else '三因子一致性'
    intro = (
        'XMM/VP为有效alpha观点；LLM仅保留审计亮牌，不参与融合分、置信度或一致性计数。'
        if audit_only else
        '每个因子独立亮牌：买入 / 卖出 / 观望。这里不代表最终下单，最终执行仍由融合分、置信度和 Gate 决定。'
    )

    s = f"""## IV. {title} ({len(signals)} 标的)

> {intro}

| 标的 | XMM观点 | VP观点 | LLM观点 | {consensus_title} | 融合信号 | Gate |
|------|---------|--------|---------|--------------|----------|------|
"""

    for sig in sorted_signals:
        sym = sig.get('symbol', '?')
        name = SYMBOL_MAP.get(sym, '')
        display_name = f"{sym}" if not name else f"{sym}<br><sub>{name}</sub>"
        xmm_view = _xmm_view(sig)
        vp_view = _vp_view(sig)
        llm_view = _llm_view(sig)
        consensus = _factor_consensus_label(sig)
        level = sig.get('fusion_level', '?')
        score = sig.get('fusion_score', 0)
        conf = sig.get('fusion_confidence', 0)
        gate = '通过' if sig.get('gate_approved', True) else '拦截'
        gate_reasons = sig.get('gate_reasons', [])
        if gate_reasons:
            reason_lines = [
                f"<sub>{_safe_cell_text(reason, 35)}</sub>"
                for reason in gate_reasons
            ]
            gate += '<br>' + '<br>'.join(reason_lines)

        s += (
            f"| {display_name} | {xmm_view} | {vp_view} | {llm_view} | {consensus} | "
            f"{SIGNAL_LEVEL_EMOJI.get(level, '⬜')}**{level}** {score:+.1f}<br><sub>置信度 {conf:.0%}</sub> | {gate} |\n"
        )

    s += "\n"
    return s


def _build_factor_decomposition(all_buy, reduced, sells):
    """三因子分解详情 — 展示 XMM/VP/LLM 各因子的原始输出和加权贡献."""
    if not all_buy and not reduced and not sells:
        return ""

    policy_signals = [*all_buy, *reduced, *sells]
    audit_only = any(_llm_is_audit_only(sig) for sig in policy_signals)
    title = '有效因子加权分解 + LLM审计' if audit_only else '三因子加权分解'
    s = f"## VI. {title}\n\n"

    for group_name, group_signals in [
        ('BUY 信号', all_buy),
        ('REDUCED 信号', reduced),
        ('SELL 信号', sells),
    ]:
        if not group_signals:
            continue

        s += f"### {group_name} ({len(group_signals)} 只)\n\n"
        llm_score_header = 'LLM审计分' if audit_only else 'LLM原始'
        llm_contrib_header = '→alpha' if audit_only else '→加权'
        s += f"| 标的 | 融合分 | XMM原始 | →加权 | VP原始 | →加权 | {llm_score_header} | {llm_contrib_header} | XMM详情 | VP详情 | LLM详情 |\n"
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
    if audit_only:
        s += """### 权重体系（audit_only）

| 因子 | 有效权重 | 角色 | 数据源 |
|------|----------|------|--------|
| **XMM** (徐小明策略) | 60% | 主力方向判断 | 双EMA趋势 + MACD结构 + TD9序列 |
| **VP** (Volume Profile) | 25% | 筹码箱体确认 | 成交量分布 + VAH/VAL/POC |
| **LLM** (技术审计) | **0% alpha** | 仅保留观点、摘要和审计分 | NVIDIA NIM / OHLC技术解释 |
| **预留权重** | **15%** | 不分配、不放大XMM/VP | Shadow A策略 |

> LLM异常或跳过时预留份额仍保持；只有XMM/VP等有效alpha源之间按可用状态调整权重。

"""
    else:
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

    s = f"""## VII. 信号变动对比 (vs {prev_date})

> 仅列出融合分变动 ≥5 或信号等级发生变化的标的

| 标的 | 前次分 | 当前分 | Δ | 前次等级 | 当前等级 |
|------|--------|--------|-----|----------|----------|
"""

    changes.sort(key=lambda x: abs(x['delta']), reverse=True)
    for c in changes:
        s += f"| {c['symbol']} | {c['prev_score']:+.1f} | {c['curr_score']:+.1f} | {c['delta']:+.0f}{'↑' if c['delta']>0 else '↓'} | {c['prev_level']} | {c['curr_level']} |\n"

    s += "\n"
    return s


def _matching_execution_result(order, execution_results):
    """Find the executor result corresponding to a generated order."""
    symbol = str(order.get('symbol', ''))
    action = str(order.get('action', '')).upper()
    for result in execution_results:
        if (
            str(result.get('symbol', '')) == symbol
            and str(result.get('action', '')).upper() == action
        ):
            return result
    return None


def _order_execution_status(order, execution_mode, execution_results):
    """Return a truthful user-facing status for a generated order."""
    mode = str(execution_mode or '').upper()
    if mode == 'LIVE_BLOCKED_BY_RULES':
        return '未执行（风控规则拦截）'
    if mode == 'LIVE_BLOCKED_BY_CONFIRM':
        return '未执行（缺少 live 确认）'
    if mode == 'SIGNAL_ONLY':
        return '仅生成信号'

    result = _matching_execution_result(order, execution_results)
    status = str((result or {}).get('status', '')).upper()
    message = str((result or {}).get('message', '')).strip()

    if mode == 'DRY_RUN':
        return '模拟执行（DRY-RUN）' if status == 'DRY-RUN' else '模拟结果缺失'
    if mode == 'LIVE_CONFIRMED':
        if status == 'FILLED_ALL':
            return '已成交'
        if status in {'SUBMITTING', 'SUBMITTED', 'CANCELLING', 'TIMEOUT', 'UNKNOWN'}:
            detail = message or status
            return f'已提交、待确认（{detail[:40]}）'
        if status in {'FILLED_PART', 'CANCELLED_PART', 'FILL_CANCELLED'}:
            detail = message or status
            return f'部分成交、终态待确认（{detail[:40]}）'
        if status:
            detail = message or status
            return f'未成交（{detail[:40]}）'
        return '未确认（无执行回执）'
    return '仅生成（执行状态未知）'


def _build_execution_tracking(orders, positions, signals, market,
                              execution_mode='', execution_results=None,
                              report_mode='', account_access=None,
                              order_api_called=None):
    """执行层追踪 — 严格区分候选订单、模拟、拦截与真实成交。"""
    s = "## VIII. 执行追踪\n\n"
    execution_results = execution_results or []

    if report_mode == 'RESEARCH_ONLY_COMPLETED_DAILY':
        return s + (
            f"**Execution Mode**: `{execution_mode or 'RESEARCH_ONLY'}`\n\n"
            "| 边界 | 状态 |\n"
            "|---|---|\n"
            f"| Account Access | `{'ENABLED' if account_access else 'DISABLED'}` |\n"
            f"| Order API Called | `{'YES' if order_api_called else 'NO'}` |\n"
            f"| Candidate Orders | `{len(orders)}` |\n"
            f"| Execution Results | `{len(execution_results)}` |\n\n"
            "研究报告模式未访问账户/持仓，未生成或执行订单。\n\n"
        )

    # 今日候选订单。orders 是意图清单，只有 FILLED_ALL 才能称为已成交。
    buy_orders = [o for o in orders if o.get('action') == 'BUY']
    sell_orders = [o for o in orders if o.get('action') in ('SELL', 'STOP', 'SIGNAL_EXIT')]
    skip_reasons = []

    for o in orders:
        if o.get('reason', '').startswith('[SKIP]'):
            skip_reasons.append(o)

    if orders:
        s += f"**Execution Mode**: `{execution_mode or 'UNKNOWN'}`\n\n"
        s += "### 订单生成与执行状态\n\n"
        s += "| 标的 | 方向 | 数量 | 价格 | 执行状态 | 原因 |\n"
        s += "|------|------|------|------|----------|------|\n"
        for order in orders:
            status = _order_execution_status(
                order, execution_mode, execution_results,
            )
            s += (
                f"| {order.get('symbol', '?')} | {order.get('action', '?')} | "
                f"{order.get('qty', 0)} | {order.get('price', 0):.2f} | "
                f"{status} | {order.get('reason', '')[:50]} |\n"
            )

    # BUY 信号未执行的原因
    signal_map = {s['symbol']: s for s in signals}
    held_syms = {p.get('symbol', '') for p in positions}
    buy_signals = [s for s in signals if s.get('fusion_level') in ('STRONG_BUY', 'BUY')]
    filled_buy_symbols = {
        result.get('symbol', '')
        for result in execution_results
        if str(result.get('action', '')).upper() == 'BUY'
        and str(result.get('status', '')).upper() == 'FILLED_ALL'
    }
    unexecuted = [
        sig for sig in buy_signals
        if sig['symbol'] not in filled_buy_symbols
    ]

    if unexecuted:
        s += "\n### BUY 信号未执行分析\n\n"
        s += "| 标的 | 信号 | 分数 | 置信度 | 跳过原因 |\n"
        s += "|------|------|------|--------|----------|\n"
        for sig in unexecuted:
            sym = sig['symbol']
            reason = ''
            generated_order = next(
                (order for order in buy_orders if order.get('symbol') == sym),
                None,
            )
            if generated_order:
                reason = _order_execution_status(
                    generated_order, execution_mode, execution_results,
                )
            elif sym in held_syms:
                reason = '已持有'
            elif sig.get('fusion_confidence', 0) < 0.65:
                reason = f'置信度不足 ({sig["fusion_confidence"]:.0%} < 65%)'
            elif sig.get('fusion_score', 0) < 40:
                reason = f'分数不足 ({sig["fusion_score"]:+.0f} < +40)'
            else:
                reason = '仓位/预算限制'
            s += f"| {sym} | {sig['fusion_level']} | {sig['fusion_score']:+.0f} | {sig.get('fusion_confidence', 0):.0%} | {reason} |\n"

    if not orders and not unexecuted:
        s += "本时段无订单生成。\n"

    s += "\n"
    return s


def _build_risk_dashboard(positions, signals, total_assets=0):
    """风控仪表板."""
    s = "## IX. 风控仪表板\n\n"

    if positions:
        s += "### 持仓状态\n\n"
        s += "> 账户现价来自 Futu 持仓查询；全量矩阵中的信号价来自信号扫描行情。\n\n"
        s += "| 标的 | 股数 | 成本价 | 账户现价 | PnL% | 信号 | 风控状态 |\n"
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
            position_pct = qty * cur / total_assets if total_assets > 0 else 0
            if p.get('triggered_stop'):
                risk_status = '触发止损'
            elif position_pct > 0.20:
                risk_status = f'超配告警 {position_pct:.1%} > 20%（不自动减仓）'
            else:
                risk_status = '正常'
            s += f"| {sym} | {qty} | {entry:.2f} | {cur:.2f} | {risk_color} {pnl:+.2f}% | {curr_level} | {risk_status} |\n"
    else:
        s += "当前无持仓。\n"

    s += "\n### 风控参数\n\n"
    s += "| 参数 | 设定值 | 说明 |\n"
    s += "|------|--------|------|\n"
    s += "| 单只目标/开仓上限 | 20% | 超配仅告警，不自动减仓 |\n"
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
    s = "## X. 策略备注\n\n"

    # 统计异常
    all_buy = [sig for sig in signals if sig.get('fusion_level') in ('STRONG_BUY', 'BUY')]
    reduced = [sig for sig in signals if sig.get('fusion_level') == 'REDUCED']
    xmm_zeros = [sig for sig in signals if sig.get('raw_scores', {}).get('xmm', 0) == 0]

    observations = []

    # XMM 零信号分析
    if len(xmm_zeros) > len(signals) * 0.5:
        if any(_llm_is_audit_only(sig) for sig in signals):
            observations.append(
                f"XMM 因子大面积中性（{len(xmm_zeros)}/{len(signals)} 标的 XMM=0）"
                "→ 趋势不明朗；LLM仅作审计，当前只有VP提供非XMM alpha，通常不足以独自触发 BUY"
            )
        else:
            observations.append(f"XMM 因子大面积中性（{len(xmm_zeros)}/{len(signals)} 标的 XMM=0）→ 趋势不明朗，VP 和 LLM 无法独自驱动 BUY 信号（因 XMM 占 60% 权重）")

    # REDUCED 比例分析
    if reduced:
        reduced_pct = len(reduced) / len(signals) if signals else 0
        if reduced_pct > 0.3:
            # 精确报告方向计数；平均分不能证明“多数”标的朝同一方向。
            reduced_scores = [sig.get('fusion_score', 0) for sig in reduced]
            positive_count = sum(score > 1e-9 for score in reduced_scores)
            negative_count = sum(score < -1e-9 for score in reduced_scores)
            neutral_count = len(reduced_scores) - positive_count - negative_count
            observations.append(
                f"REDUCED 占比 {reduced_pct:.0%} ({len(reduced)}/{len(signals)})："
                f"{positive_count}偏多 / {negative_count}偏空 / {neutral_count}中性；"
                "REDUCED 不是 BUY/SELL，仍以 Gate 与执行证据为准"
            )

        # REDUCED 根因分析
        reduced_xmm_zero = [sig for sig in reduced if sig.get('raw_scores', {}).get('xmm', 0) == 0]
        if reduced_xmm_zero:
            effective_signs = []
            for ss in reduced_xmm_zero:
                raw = ss.get('raw_scores', {})
                weights = ss.get('weights_used', {})
                combined = sum(
                    raw.get(factor, 0) * weights.get(factor, 0)
                    for factor in ('vp', 'llm')
                    if _factor_is_active(ss, factor)
                )
                if combined > 0:
                    effective_signs.append('bull')
                elif combined < 0:
                    effective_signs.append('bear')
                else:
                    effective_signs.append('neutral')
            bull_count = effective_signs.count('bull')
            bear_count = effective_signs.count('bear')
            neutral_count = effective_signs.count('neutral')
            direction_summary = (
                f"{len(reduced_xmm_zero)}/{len(reduced)} REDUCED（XMM中性子集）："
                f"{bull_count}偏多 / {bear_count}偏空 / {neutral_count}中性"
            )
            if bull_count > len(effective_signs) / 2:
                observations.append(f"{direction_summary}，整体偏多但 XMM 中性未确认趋势")
            elif bear_count > len(effective_signs) / 2:
                observations.append(f"{direction_summary}，整体偏空但未触发 SELL 阈值 (-30)")
            else:
                observations.append(f"{direction_summary}，有效因子方向分散，降级观察")

    partial_llm = [sig for sig in signals if sig.get('llm_status') == 'PARTIAL']
    if partial_llm:
        observations.append(
            f"LLM 摘要质量：{len(partial_llm)}/{len(signals)} 标的 PARTIAL；"
            "仅降级报告摘要，不改变既有融合分"
        )
    fallback_llm = [
        sig for sig in signals
        if (
            sig.get('llm_status') == 'FALLBACK'
            or sig.get('llm_route_status') == 'FALLBACK'
        )
    ]
    if fallback_llm:
        models = sorted({
            str(sig.get('llm_model') or 'UNKNOWN')
            for sig in fallback_llm
        })
        observations.append(
            f"LLM 路由状态：{len(fallback_llm)}/{len(signals)} 标的 FALLBACK；"
            f"实际模型 {', '.join(models)}；仅影响审计来源标记，不改变融合分"
        )

    # 市场状态影响
    state_limit = MARKET_STATE_LIMITS.get(market_state, 0.40)
    if market_state in ('BEAR', 'CORRECTION'):
        observations.append(f"市场状态 {market_state} → 仓位硬上限 {state_limit:.0%}，大幅压制建仓能力")
    elif market_state == 'CRAB':
        observations.append(f"市场状态 CRAB → 仓位上限 {state_limit:.0%}，震荡市宜精选信号分批建仓")
    else:
        approved_buys = [
            sig for sig in all_buy
            if sig.get('gate_approved', True)
        ]
        if approved_buys:
            observations.append(
                f"市场状态 {market_state} → 仓位上限 {state_limit:.0%}；"
                f"本轮 {len(approved_buys)} 只 BUY/STRONG_BUY 通过 Gate，"
                "是否成交仍以 execution results 为准"
            )
        else:
            observations.append(
                f"市场状态 {market_state} → 仓位上限 {state_limit:.0%}；"
                "本轮无通过 Gate 的 BUY/STRONG_BUY，不能仅凭市场状态建仓"
            )

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

def _effective_position_cell(sig: dict) -> str:
    """Show executable position after HardGate, without hiding raw target."""
    target_pos = sig.get('target_position', 0) or 0
    if sig.get('gate_approved', True):
        return f'{target_pos:.0%}'
    if target_pos:
        return '0%<br><sub>Gate拦截</sub>'
    return '0%'


def _xmm_view(sig: dict) -> str:
    """XMM 独立观点。"""
    status = sig.get('xmm_status', 'OK')
    if status != 'OK':
        return f'⚪无数据<br><sub>{status}</sub>'

    action = str(sig.get('xmm_action', 'HOLD')).upper()
    raw = sig.get('raw_scores', {}).get('xmm', 0)
    reason = sig.get('xmm_reason', '')
    trend = sig.get('xmm_trend', '')
    detail = reason or (f'趋势:{trend}' if trend else '')

    if action in ('BUY', 'STRONG_BUY') or raw >= 30:
        label = '🟢买入'
    elif action in ('SELL', 'STRONG_SELL') or raw <= -30:
        label = '🔴卖出'
    else:
        label = '⬜观望'

    return _view_cell(label, raw, detail)


def _vp_view(sig: dict) -> str:
    """VP 独立观点。"""
    status = sig.get('vp_status', 'OK')
    if status != 'OK':
        return f'⚪无数据<br><sub>{status}</sub>'

    direction = str(sig.get('vp_direction', 'HOLD')).upper()
    raw = sig.get('raw_scores', {}).get('vp', 0)
    state = sig.get('vp_state', '')
    state_map = {
        'above_box': '上轨外',
        'below_box': '下轨外',
        'inside_box': '箱体内',
    }
    detail = state_map.get(state, state)

    if direction in ('BUY', 'STRONG_BUY') or raw >= 30:
        label = '🟢买入'
    elif direction in ('SELL', 'STRONG_SELL') or raw <= -30:
        label = '🔴卖出'
    else:
        label = '⬜观望'

    return _view_cell(label, raw, detail)


def _llm_view(sig: dict) -> str:
    """LLM 独立观点。"""
    status = sig.get('llm_status', 'OK')
    if status == 'PARTIAL':
        route = (
            ' · FALLBACK'
            if sig.get('llm_route_status') == 'FALLBACK'
            else ''
        )
        return f'🟡质量降级<br><sub>PARTIAL{route}</sub>'
    if status not in ('OK', 'FALLBACK'):
        return f'⚪无数据<br><sub>{status}</sub>'

    raw = sig.get('raw_scores', {}).get('llm', sig.get('llm_sentiment', 0))
    event = sig.get('llm_event_type', '')
    summary = sig.get('llm_summary', '')
    detail = event if event and event != 'none' else summary

    if raw >= 30:
        label = '🟢买入'
    elif raw >= 10:
        label = '🟢偏多'
    elif raw <= -30:
        label = '🔴卖出'
    elif raw <= -10:
        label = '🔴偏空'
    else:
        label = '⬜观望'

    if _llm_is_audit_only(sig):
        label += '（回退审计）' if status == 'FALLBACK' else '（审计）'
    if status == 'FALLBACK':
        model = sig.get('llm_model', 'UNKNOWN')
        detail = f'FALLBACK · {model} · {detail}'

    return _view_cell(label, raw, detail)


def _factor_view_summary(sig: dict) -> str:
    """全量矩阵中的紧凑三因子观点。"""
    return '<br>'.join([
        f"XMM {_compact_view(sig, 'xmm')}",
        f"VP {_compact_view(sig, 'vp')}",
        f"{'LLM审计' if _llm_is_audit_only(sig) else 'LLM'} {_compact_view(sig, 'llm')}",
    ])


# ═══════════════════════════════════════════════════════════════
#  conf_v² — Shadow 置信度 (仅供展示，不参与 live 审批)
#
#  v2 输入与 FusionController._fuse_signals 同源：
#    raw_scores.xmm/vp/llm, xmm_action, xmm_position_size,
#    vp_direction, llm_sentiment
#
#  ⚠️ 这是 shadow 指标，不替换 fusion_confidence
# ═══════════════════════════════════════════════════════════════
_CONF_V2_BASE_WEIGHTS = {'XMM': 0.60, 'VP': 0.25, 'LLM': 0.15}


def _compute_conf_v2_shadow(sig: dict) -> tuple:
    """
    计算 v2 置信度作为 shadow 展示列。

    Returns:
        (conf_v2, delta) — 均为 float，delta = conf_v2 - fusion_confidence
        conf_v2 = -1 表示无法计算（信号数据不完整）
    """
    raw = sig.get('raw_scores', {})
    xmm_factor = raw.get('xmm', 0.0)
    vp_factor  = raw.get('vp', 0.0)
    llm_factor = raw.get('llm', 0.0)
    xmm_action = str(sig.get('xmm_action', 'HOLD')).upper()
    xmm_pos    = sig.get('xmm_position_size', 0.0)
    vp_dir     = str(sig.get('vp_direction', 'HOLD')).upper()
    llm_sent   = sig.get('llm_sentiment', 0.0)

    # 信号不完整无法计算
    if raw.get('xmm') is None and raw.get('vp') is None:
        return -1.0, 0.0

    w = _CONF_V2_BASE_WEIGHTS

    # 各源活跃性
    xmm_active = _factor_is_active(sig, 'xmm') and xmm_action != 'HOLD'
    vp_active  = _factor_is_active(sig, 'vp') and vp_dir != 'HOLD' and abs(vp_factor) > 1
    llm_active = _factor_is_active(sig, 'llm') and abs(llm_factor) > 1

    sources_active = {'XMM': xmm_active, 'VP': vp_active, 'LLM': llm_active}
    total = sum(w[s] for s in w if sources_active.get(s, False))
    if total <= 0:
        return 0.0, -sig.get('fusion_confidence', 0.0)

    wn = {s: (w[s] / total if sources_active.get(s, False) else 0.0) for s in w}

    # 各源原始置信度 (同源 live _fuse_signals)
    xmm_conf = min(max(xmm_pos, 0.1), 0.95) if xmm_active else 0.30
    vp_conf  = min(abs(vp_factor) / 100.0, 0.80)
    llm_conf = min(abs(llm_factor) / 100.0 + 0.15, 0.85)

    confidence = xmm_conf * wn['XMM'] + vp_conf * wn['VP'] + llm_conf * wn['LLM']

    # XMM 沉默惩罚
    if abs(xmm_factor) < 1:
        confidence = min(confidence, 0.55)

    # VP-XMM 方向分歧惩罚
    vp_dir_bin  = 1 if vp_dir == 'BUY' else (-1 if vp_dir == 'SELL' else 0)
    xmm_dir_bin = 1 if xmm_action == 'BUY' else (-1 if xmm_action == 'SELL' else 0)
    llm_dir_bin = 1 if llm_sent > 5 else (-1 if llm_sent < -5 else 0)

    if vp_dir_bin != 0 and xmm_dir_bin != 0 and vp_dir_bin * xmm_dir_bin < 0:
        confidence *= 0.85

    # 三因子同向奖励
    if vp_dir_bin != 0 and llm_dir_bin != 0 and xmm_dir_bin != 0:
        if vp_dir_bin == llm_dir_bin == xmm_dir_bin:
            confidence = min(confidence * 1.15, 0.95)

    conf_v2 = round(min(max(confidence, 0.0), 0.95), 3)
    old_conf = sig.get('fusion_confidence', 0.0)
    return conf_v2, round(conf_v2 - old_conf, 3)


def _compact_view(sig: dict, factor: str) -> str:
    """返回紧凑观点：买入/卖出/观望 + 分数。"""
    raw = sig.get('raw_scores', {}).get(factor, 0)
    if factor == 'xmm':
        status = sig.get('xmm_status', 'OK')
        action = str(sig.get('xmm_action', 'HOLD')).upper()
        if status != 'OK':
            return '无数据'
        if action in ('BUY', 'STRONG_BUY') or raw >= 30:
            return f'买入({raw:+.0f})'
        if action in ('SELL', 'STRONG_SELL') or raw <= -30:
            return f'卖出({raw:+.0f})'
        return f'观望({raw:+.0f})'

    if factor == 'vp':
        status = sig.get('vp_status', 'OK')
        direction = str(sig.get('vp_direction', 'HOLD')).upper()
        if status != 'OK':
            return '无数据'
        if direction in ('BUY', 'STRONG_BUY') or raw >= 30:
            return f'买入({raw:+.0f})'
        if direction in ('SELL', 'STRONG_SELL') or raw <= -30:
            return f'卖出({raw:+.0f})'
        return f'观望({raw:+.0f})'

    status = sig.get('llm_status', 'OK')
    if status == 'PARTIAL':
        return '质量降级'
    if status not in ('OK', 'FALLBACK'):
        return '无数据'
    prefix = '回退' if status == 'FALLBACK' else ''
    if raw >= 30:
        return f'{prefix}买入({raw:+.0f})'
    if raw >= 10:
        return f'{prefix}偏多({raw:+.0f})'
    if raw <= -30:
        return f'{prefix}卖出({raw:+.0f})'
    if raw <= -10:
        return f'{prefix}偏空({raw:+.0f})'
    return f'{prefix}观望({raw:+.0f})'


def _factor_consensus_label(sig: dict) -> str:
    """三因子一致性标签。"""
    directions = [
        _direction_bucket(sig, factor)
        for factor in ('xmm', 'vp', 'llm')
        if _factor_is_active(sig, factor)
    ]
    unavailable = 3 - len(directions)
    bullish = directions.count('bull')
    bearish = directions.count('bear')
    neutral = directions.count('neutral')

    if not directions:
        return '⚪无有效因子'
    if unavailable:
        if bullish and bearish:
            return '🟡有效因子分歧'
        if bullish:
            return f'🟢有效因子偏多({bullish}/{len(directions)})'
        if bearish:
            return f'🔴有效因子偏空({bearish}/{len(directions)})'
        return '⬜有效因子中性'

    if bullish == 3:
        return '🟢三因子共振多'
    if bearish == 3:
        return '🔴三因子共振空'
    if bullish >= 1 and bearish >= 1:
        return '🟡多空分歧'
    if bullish == 2 and neutral == 1:
        return '🟢两多一中'
    if bearish == 2 and neutral == 1:
        return '🔴两空一中'
    if bullish == 1 and neutral == 2:
        return '🟢一多两中'
    if bearish == 1 and neutral == 2:
        return '🔴一空两中'
    return '⬜三因子观望'


def _factor_is_active(sig: dict, factor: str) -> bool:
    """Only sources with live fusion weight contribute to report semantics."""
    status = str(sig.get(f'{factor}_status', 'OK') or 'OK').upper()
    weight = sig.get('weights_used', {}).get(factor)
    return status in ('OK', 'FALLBACK', 'NEUTRAL') and (weight is None or weight > 0)


def _llm_is_audit_only(sig: dict) -> bool:
    """识别显式审计模式，并兼容仅有权重/预留字段的历史快照。"""
    if str(sig.get('llm_alpha_mode', '')).lower() == 'audit_only':
        return True
    llm_weight = sig.get('weights_used', {}).get('llm')
    llm_reserved = sig.get('reserved_weights', {}).get('llm', 0)
    return llm_weight == 0 and llm_reserved > 0


def _direction_bucket(sig: dict, factor: str) -> str:
    """把因子观点归入 bull / bear / neutral。"""
    raw = sig.get('raw_scores', {}).get(factor, 0)
    if factor == 'xmm':
        action = str(sig.get('xmm_action', 'HOLD')).upper()
        if action in ('BUY', 'STRONG_BUY') or raw >= 30:
            return 'bull'
        if action in ('SELL', 'STRONG_SELL') or raw <= -30:
            return 'bear'
        return 'neutral'
    if factor == 'vp':
        direction = str(sig.get('vp_direction', 'HOLD')).upper()
        if direction in ('BUY', 'STRONG_BUY') or raw >= 30:
            return 'bull'
        if direction in ('SELL', 'STRONG_SELL') or raw <= -30:
            return 'bear'
        return 'neutral'
    if raw >= 10:
        return 'bull'
    if raw <= -10:
        return 'bear'
    return 'neutral'


def _view_cell(label: str, raw: float, detail: str = '') -> str:
    """格式化独立观点单元格。"""
    detail = _safe_cell_text(detail, 22)
    if detail:
        return f'{label}<br><sub>{raw:+.0f} · {detail}</sub>'
    return f'{label}<br><sub>{raw:+.0f}</sub>'


def _safe_cell_text(value: str, max_len: int) -> str:
    """Markdown 表格单元格安全文本。"""
    text = str(value or '').replace('|', ' ').replace('\n', ' ').strip()
    if len(text) > max_len:
        return text[:max_len - 2] + '..'
    return text


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
                    signals = data.get('signals', [])
                    if _valid_prev_signals(signals):
                        return signals
    except Exception:
        pass
    return []


def _valid_prev_signals(signals: list) -> bool:
    """Reject corrupted history snapshots before building signal deltas."""
    if not signals:
        return False
    symbols = [sig.get('symbol') for sig in signals if isinstance(sig, dict)]
    if len(symbols) != len(signals):
        return False
    return len(set(symbols)) == len(symbols)


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
    weights = sig.get('weights_used', {})
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
    if llm_status == 'FALLBACK':
        parts.append('LLM回退[FALLBACK]')
    elif llm_status not in ('OK', ''):
        parts.append(f'LLM异常[{llm_status}]')

    # ERROR
    if level == 'ERROR':
        return ' | '.join(parts) if parts else '数据/策略异常'

    # STRONG_BUY / BUY: 哪个因子做主要贡献
    if level in ('STRONG_BUY', 'BUY'):
        contributions = [
            (label, value)
            for factor, label, value in (
                ('xmm', 'XMM', xmm_raw),
                ('vp', 'VP', vp_raw),
                ('llm', 'LLM', llm_raw),
            )
            if _factor_is_active(sig, factor)
        ]
        contributions.sort(key=lambda x: x[1], reverse=True)
        top = contributions[0] if contributions else ('有效因子', 0)
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
            vp_weight = weights.get('vp', 0.25)
            parts.append(f'XMM中性->压分; VP偏多但权重({vp_weight:.0%})不足以触发')
        elif xmm_raw == 0 and vp_raw < 0:
            parts.append('XMM中性; VP偏空->压分')
        elif xmm_raw == 0 and vp_raw == 0 and llm_raw == 0:
            parts.append('三因子均中性(XMM=VP=0)')
        elif xmm_raw == 0 and vp_raw == 0:
            if _factor_is_active(sig, 'llm'):
                direction = '偏多' if llm_raw > 0 else '偏空'
                parts.append(f'XMM/VP中性; LLM单独{direction}({llm_raw:+.0f})')
            else:
                parts.append(f'LLM审计分{llm_raw:+.0f}(未入融合)')
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
            active = []
            combined = 0.0
            for factor, factor_raw in (('vp', vp_raw), ('llm', llm_raw)):
                if _factor_is_active(sig, factor):
                    active.append(factor.upper())
                    combined += factor_raw * weights.get(factor, 0)
            direction = '偏多' if combined > 0 else ('偏空' if combined < 0 else '中性')
            names = '+'.join(active) if active else '无有效非XMM因子'
            parts.append(f'XMM中性; 有效因子{names}{direction}({combined:+.1f})')
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
    # 数据源诊断比通用审计提示更有行动价值，优先保留在有限的根因单元格中。
    def warning_priority(warning):
        warning_text = str(warning)
        if re.search(r'insufficient data:\s*\d+\s*<\s*\d+', warning_text):
            return 0
        if re.search(r'NVIDIA NIM.*(?:超时|API错误)', warning_text, re.IGNORECASE):
            return 0
        if 'LLM处于审计模式' in warning_text:
            return 2
        return 1

    prioritized_warnings = sorted(
        warnings,
        key=warning_priority,
    )
    for w in prioritized_warnings:
        warning_text = str(w).replace('|', ' ')
        insufficient = re.search(r'insufficient data:\s*(\d+)\s*<\s*(\d+)', warning_text)
        if insufficient:
            short_w = f'VP数据不足 {insufficient.group(1)}/{insufficient.group(2)}'
        else:
            short_w = _safe_cell_text(warning_text, 40)
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

    if llm_status == 'PARTIAL':
        brief = llm_summary[:20].replace('|', ' ') if llm_summary else ''
        return f'[PARTIAL] {brief}'.rstrip()
    if llm_status not in ('OK', 'FALLBACK'):
        return f'[{llm_status}]'

    if llm_summary:
        brief = llm_summary[:20].replace('|', ' ')
        prefix = '[FALLBACK] ' if llm_status == 'FALLBACK' else ''
        return f'{prefix}{brief}'
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
