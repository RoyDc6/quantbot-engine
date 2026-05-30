#!/usr/bin/env python3
"""
report_generator.py - QuantBot 信号报告生成器 v2.0

直接调用 FusionController API 获取结构化数据，生成 Markdown 格式报告。
不再依赖 stdout 正则解析。

注: FusionEngine(fusion_framework/fusion_engine.py) 已被 FusionController
内部的 _fuse_signals() 取代，是死代码。本脚本只调用 FusionController。

用法:
    python report_generator.py --market HK
    python report_generator.py --market US
    python report_generator.py --market HK --format md    # Markdown (默认)
    python report_generator.py --market HK --format text  # 纯文本 (兼容)
"""

import sys
import os
import argparse
import json
from datetime import datetime, date
from pathlib import Path

# stdout 编码修正（Windows GBK 环境下 emoji 会炸）
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# === 路径 ===
BASE = Path('E:/quant')
sys.path.insert(0, str(BASE))

import config


# === 数据采集层 =====================================================

def collect_market_state() -> dict:
    """获取市场状态分类（VIX + 趋势 + 动量）。"""
    import io
    from contextlib import redirect_stdout
    try:
        from market_state.classifier import MarketStateClassifier
        clf = MarketStateClassifier('SPY.US')
        # 把 classifier 内部的 print 重定向到 stderr，保持 stdout 干净
        buf = io.StringIO()
        with redirect_stdout(buf):
            clf.load_data()
            report = clf.analyze()
        # classifier 的调试输出转到 stderr
        debug_text = buf.getvalue()
        if debug_text:
            print(debug_text, file=sys.stderr, end='')
        return {
            'market_state': report.get('market_state', 'CRAB'),
            'vix_regime': report.get('vix_regime', 'CANDIDATE'),
            'trend': report.get('trend', 'N/A'),
            'momentum': report.get('momentum', 'N/A'),
            'vix_percentile': report.get('vix_percentile', 0),
            'vxx_price': report.get('vxx_price', 0),
            'raw': report,
        }
    except Exception as e:
        return {
            'market_state': 'CRAB',
            'vix_regime': 'CANDIDATE',
            'trend': 'N/A',
            'momentum': 'N/A',
            'vix_percentile': 0,
            'vxx_price': 0,
            'error': str(e),
        }


def collect_signals(market: str, market_state: str) -> list:
    """调用 FusionController 扫描市场，返回结构化信号列表。"""
    import io
    from contextlib import redirect_stdout
    from core.fusion_controller import FusionController
    from core.futu_adapter import FutuAdapter
    from core.utils import to_standard_symbol

    # 把所有 stdout 重定向到 stderr，保持报告 stdout 干净
    buf = io.StringIO()
    with redirect_stdout(buf):
        adapter = FutuAdapter(host=config.FUTU_HOST, port=config.FUTU_PORT)
        ok, msg = adapter.test_connection()
        if not ok:
            debug = buf.getvalue()
            if debug:
                print(debug, file=sys.stderr, end='')
            return [], f'Futu OpenD 不可达: {msg}'

        vix_map = adapter.fetch_vix_data()

        fc = FusionController(config={
            'futu_host': config.FUTU_HOST,
            'futu_port': config.FUTU_PORT,
        })

        if market == 'HK':
            targets = list(config.UNIVERSE_HK.keys())
        elif market == 'US':
            targets = [to_standard_symbol(s) for s in config.UNIVERSE_US.keys()]
        else:
            debug = buf.getvalue()
            if debug:
                print(debug, file=sys.stderr, end='')
            return [], f'不支持的市场: {market}'

        signals = []
        for sym in targets:
            try:
                result = fc.analyze_ticker(
                    ticker=sym,
                    market_state=market_state,
                    vix_data=vix_map,
                )
                signals.append(_normalize_signal(result, market, sym))
            except Exception as e:
                signals.append({
                    'symbol': sym, 'market': market, 'status': 'ERROR',
                    'error': str(e), 'fusion_level': 'HOLD', 'fusion_score': 0,
                    'fusion_confidence': 0, 'close': 0,
                })

    # classifier 的调试输出转到 stderr
    debug_text = buf.getvalue()
    if debug_text:
        print(debug_text, file=sys.stderr, end='')

    # 按 score 降序排列
    signals.sort(key=lambda x: x.get('fusion_score', 0), reverse=True)
    return signals, None


def collect_account(market: str) -> dict:
    """获取 Futu 账户信息和持仓。"""
    import io
    from contextlib import redirect_stdout
    try:
        from core.futu_adapter import FutuAdapter
        buf = io.StringIO()
        with redirect_stdout(buf):
            adapter = FutuAdapter(host=config.FUTU_HOST, port=config.FUTU_PORT)
            account = adapter.get_account_info(market)
            positions = adapter.get_positions(market)
        debug = buf.getvalue()
        if debug:
            print(debug, file=sys.stderr, end='')
        return {'account': account, 'positions': positions}
    except Exception as e:
        return {'account': None, 'positions': [], 'error': str(e)}


def collect_ai_analysis(market: str, market_state: dict, signals: list,
                        account_data: dict) -> dict:
    """调用 NVIDIA NIM LLM 生成 AI 评估摘要。"""
    import io
    from contextlib import redirect_stdout
    import sys as _sys

    market_label = '港股' if market == 'HK' else '美股'

    # 构建信号摘要
    sig_lines = []
    for s in signals:
        raw = s.get('raw_scores', {})
        sig_lines.append(
            f'{s["symbol"]}: {s["fusion_level"]} score={s["fusion_score"]:+.1f} '
            f'conf={s["fusion_confidence"]:.0%} RSI={s["rsi_daily"]:.0f}/{s["rsi_weekly"]:.0f} '
            f'XMM={raw.get("xmm",0):+.0f} VP={raw.get("vp",0):+.0f} LLM={raw.get("llm",0):+.0f}'
        )

    # 持仓摘要
    acct = account_data.get('account') or {}
    positions = account_data.get('positions') or []
    pos_lines = []
    for p in positions:
        code = p.get('code', '?')
        cost = p.get('cost_price', 0)
        cur = p.get('current_price', 0)
        pnl = (cur / cost - 1) * 100 if cost > 0 else 0
        pos_lines.append(f'{code}: {p.get("qty",0):.0f}股 成本{cost:.2f} 现价{cur:.2f} 盈亏{pnl:+.1f}%')

    ms = market_state
    prompt = f"""你是 QuantBot 量化分析引擎。根据以下{market_label}数据生成简短评估。

## 市场状态
- 状态: {ms.get('market_state','?')} | VIX: {ms.get('vix_regime','?')} (VXX={ms.get('vxx_price',0):.2f} 分位{ms.get('vix_percentile',0):.0%})
- 趋势: {ms.get('trend','?')} | 动量: {ms.get('momentum','?')}

## 账户
- 总资产: {acct.get('total_assets',0):,.0f} | 现金: {acct.get('cash',0):,.0f} | 持仓市值: {acct.get('market_val',0):,.0f}
{chr(10).join(pos_lines) if pos_lines else '- 空仓'}

## 信号明细
{chr(10).join(sig_lines)}

## 输出要求（中文，每段1-2句话）
请按以下格式输出，不要加 markdown 标记：

市场综述：（一句话概括当前市场环境和操作难度）

信号点评：（对主要 BUY/SELL 信号逐一点评，指出关键分歧）

持仓建议：（基于持仓盈亏和信号给出具体操作建议）

风险提示：（当前最值得关注的 1-2 个风险）

总体评级：（看多/看空/中性 + 置信度百分比）"""

    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            sys.path.insert(0, r'C:\Users\RoyGoode\.workbuddy\skills\nvidia-api\scripts')
            from nvidia_api import nvidia_llm
            result = nvidia_llm.chat(
                prompt,
                model='meta/llama-4-maverick-17b-128e-instruct',
                max_tokens=600,
                temperature=0.4,
            )
        debug = buf.getvalue()
        if debug:
            print(debug, file=_sys.stderr, end='')
        return {'analysis': result, 'error': None}
    except Exception as e:
        return {'analysis': None, 'error': str(e)}


def _normalize_signal(fc_result: dict, market: str, sym: str) -> dict:
    """统一信号格式。"""
    fusion = fc_result.get('fusion', {})
    directive = fc_result.get('directive', {})
    status = fc_result.get('status', {})
    warnings = fc_result.get('warnings', []) + fusion.get('warnings', [])

    return {
        'symbol': sym,
        'market': market,
        'close': fc_result.get('close', 0),
        'data_source': fc_result.get('data_source', '?'),
        'fusion_level': directive.get('level', fusion.get('level', 'HOLD')),
        'fusion_score': fusion.get('score', 0),
        'fusion_confidence': fusion.get('confidence', 0),
        'target_position': fusion.get('position_pct', 0),
        'risk': fusion.get('risk_level', 'MEDIUM'),
        'reasoning': fusion.get('reasoning', ''),
        'raw_scores': fusion.get('raw_scores', {}),
        'weights_used': fusion.get('weights_used', {}),
        'rsi_daily': fc_result.get('rsi_daily', 50),
        'rsi_weekly': fc_result.get('rsi_weekly', 50),
        'market_state': fc_result.get('market_state', 'CRAB'),
        'status': status,
        'warnings': warnings,
        'stale': any('滞后' in w or 'STALE' in w for w in warnings),
        'expired': any('过期' in w or 'EXPIRED' in w for w in warnings),
    }


# === Markdown 报告生成 ==============================================

def generate_markdown(market: str, market_state: dict, signals: list,
                      account_data: dict, scan_error: str = None,
                      ai_analysis: dict = None) -> str:
    """生成 Markdown 格式报告。"""
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    market_label = '港股' if market == 'HK' else '美股'

    # 分组
    strong_buys = [s for s in signals if s['fusion_level'] == 'STRONG_BUY']
    buys = [s for s in signals if s['fusion_level'] == 'BUY']
    reduced = [s for s in signals if s['fusion_level'] == 'REDUCED']
    holds = [s for s in signals if s['fusion_level'] == 'HOLD']
    sells = [s for s in signals if s['fusion_level'] in ('SELL', 'STRONG_SELL')]
    stale = [s for s in signals if s.get('stale')]
    errors = [s for s in signals if s.get('status') == 'ERROR']

    # 账户
    acct = account_data.get('account') or {}
    positions = account_data.get('positions') or []
    total_assets = acct.get('total_assets', 0)
    cash = acct.get('cash', 0)
    market_val = acct.get('market_val', 0)

    lines = []

    # ── 标题 ──
    lines.append(f'# {market_label}信号报告 {today}')
    lines.append('')

    # ── 市场状态 ──
    ms = market_state.get('market_state', '?')
    vr = market_state.get('vix_regime', '?')
    trend = market_state.get('trend', '?')
    mom = market_state.get('momentum', '?')
    vix_pct = market_state.get('vix_percentile', 0)
    vxx = market_state.get('vxx_price', 0)

    ms_icon = {'BULL': '🟢', 'BEAR': '🔴', 'CRAB': '🟡',
               'RECOVERY': '🔵', 'CORRECTION': '🟠'}.get(ms, '⚪')

    lines.append(f'## {ms_icon} 市场状态: {ms}')
    lines.append('')
    lines.append(f'| 维度 | 状态 |')
    lines.append(f'|------|------|')
    lines.append(f'| VIX 环境 | {vr} |')
    if vxx > 0:
        lines.append(f'| VXX 价格 | {vxx:.2f} (分位 {vix_pct:.0%}) |')
    lines.append(f'| 趋势 | {trend} |')
    lines.append(f'| 动量 | {mom} |')

    if market_state.get('error'):
        lines.append(f'| 数据源 | ⚠️ {market_state["error"]} |')
    lines.append('')

    # ── 账户概览 ──
    if total_assets > 0:
        pos_pct = market_val / total_assets if total_assets > 0 else 0
        lines.append(f'## 💰 账户概览')
        lines.append('')
        lines.append(f'| 指标 | 数值 |')
        lines.append(f'|------|------|')
        lines.append(f'| 总资产 | {total_assets:,.0f} |')
        lines.append(f'| 现金 | {cash:,.0f} ({1-pos_pct:.1%}) |')
        lines.append(f'| 持仓市值 | {market_val:,.0f} ({pos_pct:.1%}) |')
        lines.append(f'| 持仓数 | {len(positions)} |')
        lines.append('')

    # ── 扫描错误 ──
    if scan_error:
        lines.append(f'## ❌ 扫描错误')
        lines.append(f'')
        lines.append(f'```')
        lines.append(scan_error)
        lines.append(f'```')
        lines.append('')

    # ── 信号汇总 ──
    total = len(signals)
    lines.append(f'## 📊 信号汇总 (共 {total} 只)')
    lines.append('')
    lines.append(f'| 类型 | 数量 |')
    lines.append(f'|------|------|')
    if strong_buys:
        lines.append(f'| STRONG_BUY | {len(strong_buys)} |')
    if buys:
        lines.append(f'| BUY | {len(buys)} |')
    if reduced:
        lines.append(f'| REDUCED | {len(reduced)} |')
    lines.append(f'| HOLD | {len(holds)} |')
    if sells:
        lines.append(f'| SELL | {len(sells)} |')
    if errors:
        lines.append(f'| ERROR | {len(errors)} |')
    lines.append('')

    # ── BUY 信号详情 ──
    buy_all = strong_buys + buys
    if buy_all:
        lines.append(f'## 🟢 BUY 信号 ({len(buy_all)} 只)')
        lines.append('')
        lines.append(f'| 标的 | 信号 | 评分 | 置信度 | 现价 | RSI(日/周) | XMM | VP | LLM |')
        lines.append(f'|------|------|------|--------|------|-----------|-----|-----|-----|')
        for s in buy_all:
            raw = s.get('raw_scores', {})
            xmm = raw.get('xmm', 0)
            vp = raw.get('vp', 0)
            llm = raw.get('llm', 0)
            stale_mark = ' [S]' if s.get('stale') else ''
            lines.append(
                f'| {s["symbol"]}{stale_mark} | {s["fusion_level"]} '
                f'| {s["fusion_score"]:+.1f} | {s["fusion_confidence"]:.0%} '
                f'| {s["close"]:.2f} | {s["rsi_daily"]:.0f}/{s["rsi_weekly"]:.0f} '
                f'| {xmm:+.0f} | {vp:+.0f} | {llm:+.0f} |'
            )
        lines.append('')

    # ── SELL 信号详情 ──
    if sells:
        lines.append(f'## 🔴 SELL 信号 ({len(sells)} 只)')
        lines.append('')
        lines.append(f'| 标的 | 信号 | 评分 | 风险 | 置信度 | XMM | VP | LLM |')
        lines.append(f'|------|------|------|------|--------|-----|-----|-----|')
        for s in sells:
            raw = s.get('raw_scores', {})
            stale_mark = ' [S]' if s.get('stale') else ''
            lines.append(
                f'| {s["symbol"]}{stale_mark} | {s["fusion_level"]} '
                f'| {s["fusion_score"]:+.1f} | {s.get("risk", "N/A")} '
                f'| {s["fusion_confidence"]:.0%} '
                f'| {raw.get("xmm", 0):+.0f} | {raw.get("vp", 0):+.0f} '
                f'| {raw.get("llm", 0):+.0f} |'
            )
        lines.append('')

    # ── REDUCED 信号 ──
    if reduced:
        lines.append(f'## 🟡 REDUCED 信号 ({len(reduced)} 只)')
        lines.append('')
        lines.append(f'| 标的 | 评分 | 置信度 | XMM | VP | LLM |')
        lines.append(f'|------|------|--------|-----|-----|-----|')
        for s in reduced:
            raw = s.get('raw_scores', {})
            stale_mark = ' [S]' if s.get('stale') else ''
            lines.append(
                f'| {s["symbol"]}{stale_mark} | {s["fusion_score"]:+.1f} '
                f'| {s["fusion_confidence"]:.0%} '
                f'| {raw.get("xmm", 0):+.0f} | {raw.get("vp", 0):+.0f} '
                f'| {raw.get("llm", 0):+.0f} |'
            )
        lines.append('')

    # ── 持仓明细 ──
    if positions:
        lines.append(f'## 💼 当前持仓 ({len(positions)} 只)')
        lines.append('')
        lines.append(f'| 标的 | 股数 | 成本价 | 现价 | 盈亏 |')
        lines.append(f'|------|------|--------|------|------|')
        for p in positions:
            code = p.get('code', '?')
            # 转换 Futu 代码格式: HK.00700 -> 00700.HK
            parts = code.split('.')
            if len(parts) == 2:
                sym = f'{parts[1]}.{parts[0]}'
            else:
                sym = code
            qty = p.get('qty', 0)
            cost = p.get('cost_price', 0)
            cur = p.get('current_price', 0)
            pnl = (cur / cost - 1) * 100 if cost > 0 else 0
            pnl_icon = '+' if pnl >= 0 else ''
            lines.append(
                f'| {sym} | {qty:.0f} | {cost:.2f} | {cur:.2f} '
                f'| {pnl_icon}{pnl:.1f}% |'
            )
        lines.append('')

    # ── 数据新鲜度告警 ──
    if stale:
        lines.append(f'## ⚠️ 数据新鲜度告警')
        lines.append('')
        for s in stale:
            warns = [w for w in s.get('warnings', []) if '滞后' in w or 'STALE' in w]
            for w in warns:
                lines.append(f'- **{s["symbol"]}**: {w}')
        lines.append('')

    # ── 错误标的 ──
    if errors:
        lines.append(f'## ❌ 异常标的')
        lines.append('')
        for s in errors:
            lines.append(f'- **{s["symbol"]}**: {s.get("error", "未知错误")}')
        lines.append('')

    # ── 信号 reasoning 摘要 ──
    reasoning_signals = [s for s in buy_all + sells if s.get('reasoning')]
    if reasoning_signals:
        lines.append(f'## 📝 信号解读')
        lines.append('')
        for s in reasoning_signals[:5]:  # 最多5条
            lines.append(f'- **{s["symbol"]}** ({s["fusion_level"]}): {s["reasoning"]}')
        lines.append('')

    # ── AI 评估 ──
    if ai_analysis:
        lines.append(f'## 🤖 AI 评估')
        lines.append('')
        if ai_analysis.get('error'):
            lines.append(f'> AI 分析暂不可用: {ai_analysis["error"]}')
        elif ai_analysis.get('analysis'):
            # 按段落渲染，保留原始文本结构
            for para in ai_analysis['analysis'].strip().split('\n'):
                para = para.strip()
                if para:
                    lines.append(para)
        lines.append('')

    # ── 风控参数 ──
    lines.append(f'## ⚙️ 风控参数')
    lines.append('')
    lines.append(f'| 参数 | 值 |')
    lines.append(f'|------|-----|')
    lines.append(f'| 固定止损 | {config.FIXED_STOP_PCT:.0%} |')
    lines.append(f'| 移动止损 | {config.TRAILING_STOP_PCT:.0%} |')
    lines.append(f'| 组合回撤限制 | {config.PORTFOLIO_DD_PCT:.0%} |')
    lines.append(f'| 单只仓位上限 | {config.MAX_POSITION_PCT:.0%} |')
    lines.append(f'| 总仓位上限 | {config.MAX_TOTAL_PCT:.0%} |')
    lines.append(f'| 止盈(港/美) | {config.TAKE_PROFIT_PCT_HK:.0%}/{config.TAKE_PROFIT_PCT_US:.0%} |')
    lines.append('')

    lines.append(f'---')
    lines.append(f'*Generated by QuantBot v2.0 | {today}*')

    return '\n'.join(lines)


# === 纯文本报告（兼容模式）==========================================

def generate_text(market: str, market_state: dict, signals: list,
                  account_data: dict, scan_error: str = None) -> str:
    """生成纯文本格式报告（兼容旧版输出）。"""
    today = datetime.now().strftime('%Y-%m-%d')
    market_label = '港股' if market == 'HK' else '美股'
    sep = '━' * 50

    lines = [f'━━━ {market_label}扫描报告 {today} ━━━', '']

    # 市场状态
    ms = market_state.get('market_state', '?')
    vr = market_state.get('vix_regime', '?')
    vxx = market_state.get('vxx_price', 0)
    vix_pct = market_state.get('vix_percentile', 0)
    lines.append('【市场状态】')
    lines.append(f'  状态: {ms} | VIX: {vr} | VXX: {vxx:.2f} (分位 {vix_pct:.0%})')
    lines.append(f'  趋势: {market_state.get("trend", "?")} | 动量: {market_state.get("momentum", "?")}')
    lines.append('')

    # 信号分组
    groups = {
        'STRONG_BUY': [], 'BUY': [], 'REDUCED': [],
        'HOLD': [], 'SELL': [], 'STRONG_SELL': [],
    }
    for s in signals:
        level = s.get('fusion_level', 'HOLD')
        if level in groups:
            groups[level].append(s)

    lines.append(f'【各标的信号】（共{len(signals)}只）')
    lines.append('')
    lines.append(f'  {"标的":<12} {"信号":<14} {"评分":<8} {"置信度":<8} {"详情"}')
    lines.append(f'  {"─"*10} {"─"*12} {"─"*6} {"─"*6} {"─"*30}')

    for group_name in ['STRONG_BUY', 'BUY', 'REDUCED', 'HOLD', 'SELL', 'STRONG_SELL']:
        sigs = groups.get(group_name, [])
        if not sigs:
            continue
        lines.append('')
        lines.append(f'  ─── {group_name} ({len(sigs)}只) ───')
        for s in sigs:
            raw = s.get('raw_scores', {})
            detail = f'conf={s["fusion_confidence"]:.0%}'
            if raw:
                detail += f' xmm={raw.get("xmm",0):+.0f} vp={raw.get("vp",0):+.0f} llm={raw.get("llm",0):+.0f}'
            stale_mark = ' [STALE]' if s.get('stale') else ''
            lines.append(f'  {s["symbol"]:<12} {s["fusion_level"]:<14} {s["fusion_score"]:+.1f}  {s["fusion_confidence"]:.0%}    {detail}{stale_mark}')

    lines.append('')
    lines.append(sep)
    return '\n'.join(lines)


# === 入口 ===========================================================

def main():
    parser = argparse.ArgumentParser(description='QuantBot 信号报告生成器 v2.0')
    parser.add_argument('--market', required=True, choices=['HK', 'US'],
                        help='市场代码')
    parser.add_argument('--format', choices=['md', 'text'], default='md',
                        help='输出格式: md=Markdown(默认), text=纯文本(兼容)')
    parser.add_argument('--no-ai', action='store_true',
                        help='跳过 AI 评估（默认启用）')
    args = parser.parse_args()

    market = args.market.upper()
    market_label = '港股' if market == 'HK' else '美股'

    print(f'正在采集 {market_label} 数据...', file=sys.stderr, flush=True)

    # 1. 市场状态
    print(f'  [1/4] 市场状态分类...', file=sys.stderr, flush=True)
    ms_data = collect_market_state()
    market_state = ms_data.get('market_state', 'CRAB')

    # 2. 信号扫描
    print(f'  [2/4] FusionController 信号扫描...', file=sys.stderr, flush=True)
    signals, scan_error = collect_signals(market, market_state)

    # 3. 账户信息
    print(f'  [3/4] Futu 账户查询...', file=sys.stderr, flush=True)
    account_data = collect_account(market)

    # 4. AI 评估
    ai_result = None
    if args.format == 'md' and not args.no_ai:
        print(f'  [4/4] AI 评估 (NVIDIA NIM)...', file=sys.stderr, flush=True)
        ai_result = collect_ai_analysis(market, ms_data, signals, account_data)

    # 5. 生成报告
    if args.format == 'md':
        report = generate_markdown(market, ms_data, signals, account_data, scan_error, ai_result)
    else:
        report = generate_text(market, ms_data, signals, account_data, scan_error)

    print(f'\n报告生成完毕 ({len(signals)} 只标的' +
          (f', AI评估: {"OK" if ai_result and not ai_result.get("error") else "SKIP"}' if args.format == "md" else '') +
          ')', file=sys.stderr, flush=True)

    # stdout 输出报告
    print(report)


if __name__ == '__main__':
    main()
