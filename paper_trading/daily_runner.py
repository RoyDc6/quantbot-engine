# -*- coding: utf-8 -*-
"""
[已归档] 纸交易 - 每日信号生成 Runner v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
此文件已于 2026-05-21 正式归档，停止使用。
替代方案:
  → 港股/美股 交易:  python unified_runner.py --market HK|US [--live]
  → 日报生成:          reports/reporter.py (由 unified_runner.py 自动调用)
  → 加密市场:          python crypto/crypto_runner.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import sys, io, os, json, time
from pathlib import Path
from datetime import datetime

# ═══ UTF-8 编码修复（PowerShell 管道兼容性）════
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from core.paths import PROJECT_ROOT
BASE = PROJECT_ROOT
PT   = BASE / 'paper_trading'
FW   = BASE / 'fusion_framework'
SKILL = Path('C:/Users/RoyGoode/.workbuddy/skills/xmm-strategy/scripts')

sys.path.insert(0, str(FW))
sys.path.insert(0, str(SKILL))

import warnings
warnings.filterwarnings('ignore')
import pandas as pd
import numpy as np
import tickflow_env  # noqa: auto-inject TICKFLOW_API_KEY
from tickflow import TickFlow

# Futu API（主力数据源 — 纸交易在 Futu 进行，行情也从 Futu 拉）
try:
    import futu as ft
    FUTU_AVAILABLE = True
except ImportError:
    FUTU_AVAILABLE = False

FUTU_HOST = '127.0.0.1'
FUTU_PORT = 11111

from core.fusion_controller import FusionController, _rsi, _weekly_rsi
from signal_types import SignalLevel, POSITION_MAP
from market_state.classifier import MarketStateClassifier, get_market_advice, VixRegime, TrendState, MarketState
from fusion_framework.hard_gate import HardGate, GateResult
from fusion_framework.signal_types import DecisionSignal, OrderDirective, Order

# === 配置 ===================================================
TARGETS = {
    'SPX': ['SPY.US'],
    'HK': ['00700.HK', '09988.HK', '03690.HK', '01024.HK', '01810.HK', '00981.HK', '02513.HK'],
}

# === 纸交易建仓常量（提前定义）================================
MAX_POSITIONS      = 5       # 最大同时持仓数
MAX_TOTAL_EXPOSURE = 0.80   # 最大总仓位占比
MIN_ENTRY_SCORE   = 45      # 最小入场分数阈值
MIN_SHARES        = 100     # 最小买入股数
STRONG_BUY_ALLOCATION = 0.20  # STRONG_BUY 每个标的固定分配仓位比例
CASH_RESERVE          = 0.20  # 预留现金比例（应对高价股整手约束）

# === 优化配置（新增）========================================
# 市场状态 → 仓位上限
MARKET_STATE_POSITION_LIMIT = {
    'BULL': 0.80,
    'RECOVERY': 0.60,
    'CRAB': 0.40,
    'CORRECTION': 0.20,
    'BEAR': 0.10,
}

# 信号质量阈值
SIGNAL_QUALITY_THRESHOLD = {
    'STRONG_BUY': {'min_score': 50, 'min_confidence': 0.75},
    'BUY': {'min_score': 40, 'min_confidence': 0.65},
    'SELL': {'min_score': -40, 'min_confidence': 0.65},
    'STRONG_SELL': {'min_score': -50, 'min_confidence': 0.75},
}

# 止损配置（启用ATR自适应）
STOP_CONFIG = {
    'fixed': -0.12,
    'trailing': -0.12,
    'portfolio_dd': -0.30,
    'atr_adaptive': False,  # 关闭ATR自适应（P0-5: Roy认为事后优化，实盘无效）
}

# ATR自适应止损参数
ATR_LOW_VOL_THRESH = 0.025  # ATR < 2.5% = 低波动
ATR_LOW_STOP_PCT = -0.15      # 低波动放宽到-15%
ATR_HIGH_STOP_PCT = -0.10     # 高波动收紧到-10%

# 风险控制配置
RISK_CONFIG = {
    'max_single_stock': 0.20,      # 单只股票最大20%
    'max_total_position': 0.80,    # 总仓位最大80%
    'max_drawdown': -0.30,         # 最大回撤-30%
    'max_daily_loss': -0.05,       # 单日最大亏损-5%
}

# 建仓配置
BUILD_CONFIG = {
    'max_positions': 5,
    'min_position_value': 10000,  # 最小建仓1万
    'strong_buy_allocation': 0.20,
    'buy_allocation': 0.10,
}

# 分批建仓配置 (P0-2)
STAGED_ENTRY_CONFIG = {
    'enabled': True,           # 启用分批建仓
    'total_tranches': 3,       # 分3批建仓
    'first_pct': 0.34,         # 首批建仓比例 (1/3)
    'min_gap_days': 1,         # 批次间最少间隔交易日
    'confirm_conditions': {    # 加仓确认条件
        'pnl_above': -0.02,    # 持仓浮亏不超过-2%才加仓
        'signal_still_buy': True,  # 信号仍为BUY/STRONG_BUY
    },
}

# 信号优先级
SIGNAL_PRIORITY = {
    'STRONG_BUY': 100,
    'BUY': 80,
    'HOLD': 50,
    'REDUCED': 30,
    'SELL': 20,
    'STRONG_SELL': 10,
}

# === 原有配置 =============================================
STOP_FIXED   = -0.12
STOP_TRAIL   = -0.12
STOP_DD      = -0.30

# === 加载 VIX ==============================================
vxx_path = BASE / 'scanner' / 'cache' / 'VXX_US.json'
vix_map = {}
if vxx_path.exists():
    with open(vxx_path) as f:
        vxx = json.load(f)
    for r in vxx:
        d = datetime.fromtimestamp(r['date']/1000).strftime('%Y-%m-%d')
        vix_map[d] = float(r['close'])

# === 初始化 FusionController =================================
fc = FusionController()
# VIX 数据通过 volt_ticker 配置注入，或由 analyze_ticker 的 vix_data 参数传入

tf = TickFlow() if os.environ.get('TICKFLOW_API_KEY') else TickFlow.free()
hard_gate = HardGate()  # 硬门槛检查引擎（决策层纯规则，零LLM）

# === Futu 数据获取（主力源）===================================
def fetch_klines_futu(symbol, count=252):
    """从 Futu OpenD 获取 K 线数据。symbol 格式: 00700.HK → HK.00700"""
    if not FUTU_AVAILABLE:
        return None
    # 转换格式: 00700.HK → HK.00700, SPY.US → US.SPY
    parts = symbol.split('.')
    if len(parts) == 2:
        futu_code = f'{parts[1]}.{parts[0]}'
    else:
        return None
    try:
        quote_ctx = ft.OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
        try:
            # Futu OpenD 要求先订阅 K_DAY 才能获取 K 线数据
            ret_sub, _ = quote_ctx.subscribe([futu_code], [ft.SubType.K_DAY])
            if ret_sub != ft.RET_OK:
                return None
            ret, data = quote_ctx.get_cur_kline(futu_code, count, ft.KLType.K_DAY, ft.AuType.QFQ)
            if ret != ft.RET_OK or data is None or len(data) == 0:
                return None
            result = {
                'timestamp': [int(datetime.strptime(str(t), '%Y-%m-%d %H:%M:%S').timestamp() * 1000)
                              if isinstance(t, str) else int(pd.Timestamp(t).timestamp() * 1000)
                              for t in data['time_key']],
                'open': [float(v) for v in data['open']],
                'high': [float(v) for v in data['high']],
                'low': [float(v) for v in data['low']],
                'close': [float(v) for v in data['close']],
                'volume': [float(v) for v in data['volume']],
            }
            return result
        finally:
            quote_ctx.close()
    except Exception as e:
        return None

# === 优化函数（新增）========================================
def filter_signal(signal, market_state):
    """过滤低质量信号"""
    level = signal['fusion_level']
    score = signal['fusion_score']
    confidence = signal['fusion_confidence']
    
    # 获取阈值
    threshold = SIGNAL_QUALITY_THRESHOLD.get(level)
    if not threshold:
        return False, f'未知信号类型: {level}'
    
    # 检查分数和置信度
    if abs(score) < threshold['min_score']:
        return False, f'分数不足: {score} < {threshold["min_score"]}'
    
    if confidence < threshold['min_confidence']:
        return False, f'置信度不足: {confidence} < {threshold["min_confidence"]}'
    
    return True, 'OK'

def check_risk_limits(portfolio, market_state):
    """检查风险限制"""
    total_value = portfolio['cash'] + sum(p['shares'] * p['current_price'] for p in portfolio['positions'])
    if total_value <= 0:
        return False, 'OK'

    # 检查单只股票仓位
    for pos in portfolio['positions']:
        stock_value = pos['shares'] * pos['current_price']
        stock_pct = stock_value / total_value
        if stock_pct > RISK_CONFIG['max_single_stock']:
            return True, f'单只股票仓位过高: {pos["symbol"]} {stock_pct:.1%} > {RISK_CONFIG["max_single_stock"]:.0%}'

    # 检查 total_position_pct
    total_position = sum(p['shares'] * p['current_price'] for p in portfolio['positions'])
    total_position_pct = total_position / total_value
    if total_position_pct > RISK_CONFIG['max_total_position']:
        return True, f'总仓位过高: {total_position_pct:.1%} > {RISK_CONFIG["max_total_position"]:.0%}'

    # 检查组合回撤
    portfolio_peak = portfolio.get('peak_value', total_value)
    if portfolio_peak > 0:
        drawdown = (total_value - portfolio_peak) / portfolio_peak
        if drawdown <= RISK_CONFIG['max_drawdown']:
            return True, f'组合回撤过大: {drawdown:.1%} <= {RISK_CONFIG["max_drawdown"]:.0%}'

    return False, 'OK'

def check_stop_loss_adaptive(position, current_price, atr_pct):
    """检查止损（含ATR自适应）"""
    entry_price = position['entry_price']
    highest_price = position['highest']
    
    # 固定止损
    pnl = (current_price / entry_price - 1)
    if pnl <= STOP_CONFIG['fixed']:
        return True, f'固定止损: {pnl:.1%}'
    
    # 移动止损
    dd = (current_price / highest_price - 1)
    if dd <= STOP_CONFIG['trailing']:
        return True, f'移动止损: {dd:.1%}'
    
    # ATR自适应止损
    if STOP_CONFIG['atr_adaptive']:
        if atr_pct < ATR_LOW_VOL_THRESH:
            stop_pct = ATR_LOW_STOP_PCT
        else:
            stop_pct = ATR_HIGH_STOP_PCT
        
        if pnl <= stop_pct:
            return True, f'ATR自适应止损: {pnl:.1%} (ATR={atr_pct:.1%})'
    
    return False, 'OK'

def rank_signals(signals, market_state):
    """对信号进行排序"""
    ranked = []
    
    for signal in signals:
        level = signal['fusion_level']
        score = signal['fusion_score']
        confidence = signal['fusion_confidence']
        
        # 基础优先级
        priority = SIGNAL_PRIORITY.get(level, 50)
        
        # 信号质量调整
        if confidence >= 0.8:
            priority *= 1.2  # 高置信度提升优先级
        elif confidence < 0.6:
            priority *= 0.8  # 低置信度降低优先级
        
        ranked.append({
            'signal': signal,
            'priority': priority,
            'rank': 0,
        })
    
    # 按优先级排序
    ranked.sort(key=lambda x: x['priority'], reverse=True)
    
    # 分配排名
    for i, item in enumerate(ranked):
        item['rank'] = i + 1
    
    return ranked

# === 分批建仓管理 =========================================
def get_staged_state(portfolio):
    """获取分批建仓状态（从 portfolio 的 staged_entries 字段）"""
    if 'staged_entries' not in portfolio:
        portfolio['staged_entries'] = {}
    return portfolio['staged_entries']

def register_staged_entry(portfolio, symbol, total_shares, total_cost, tranche_pct):
    """注册一个新的分批建仓计划"""
    staged = get_staged_state(portfolio)
    first_shares = int(total_shares * tranche_pct / 100) * 100
    if first_shares < 100:
        first_shares = min(100, total_shares)
    staged[symbol] = {
        'target_shares': total_shares,
        'filled_shares': first_shares,
        'remaining_shares': total_shares - first_shares,
        'tranches_filled': 1,
        'total_tranches': STAGED_ENTRY_CONFIG['total_tranches'],
        'last_fill_date': today,
        'avg_price': 0,  # will be updated
        'original_score': 0,
        'original_level': '',
    }
    return first_shares

def check_staged_topup(portfolio, all_signals_map):
    """检查已有分批建仓是否需要加仓，返回需要加仓的列表"""
    staged = get_staged_state(portfolio)
    to_topup = []

    for symbol, state in staged.items():
        # 已全部建完
        if state['remaining_shares'] <= 0:
            continue

        # 检查批次间隔
        last_fill = datetime.strptime(state['last_fill_date'], '%Y-%m-%d')
        days_since = (datetime.strptime(today, '%Y-%m-%d') - last_fill).days
        if days_since < STAGED_ENTRY_CONFIG['min_gap_days']:
            continue

        # 找到当前持仓
        pos = None
        for p in portfolio['positions']:
            if p['symbol'] == symbol:
                pos = p
                break

        if not pos:
            # 持仓已止损清空，取消分批计划
            state['remaining_shares'] = 0
            continue

        # 检查确认条件
        conds = STAGED_ENTRY_CONFIG['confirm_conditions']

        # 条件1: 浮亏不超过阈值
        if pos['pnl'] / 100 < conds['pnl_above']:
            print(f'  ⏸  HOLD TOPUP: {symbol} 浮亏 {pos["pnl"]:.1f}% > 阈值 {conds["pnl_above"]:.0%}，暂停加仓')
            continue

        # 条件2: 信号仍为买入
        if conds['signal_still_buy']:
            sig = all_signals_map.get(symbol)
            if not sig or sig['fusion_level'] not in ('STRONG_BUY', 'BUY'):
                print(f'  ⏸  HOLD TOPUP: {symbol} 信号已非BUY，取消加仓')
                state['remaining_shares'] = 0
                continue

        to_topup.append({
            'symbol': symbol,
            'pos': pos,
            'state': state,
            'signal': all_signals_map.get(symbol),
        })

    return to_topup

def execute_topup(portfolio, topup_item, available_cash, pos_value, total_value):
    """执行一次加仓"""
    symbol = topup_item['symbol']
    state = topup_item['state']
    pos = topup_item['pos']
    price = pos['current_price']

    # 计算本次加仓股数
    tranches_left = state['total_tranches'] - state['tranches_filled']
    if tranches_left <= 0:
        return False, available_cash, pos_value

    next_shares = int(state['remaining_shares'] / tranches_left / 100) * 100
    if next_shares < 100:
        next_shares = state['remaining_shares']

    cost = next_shares * price
    if cost > available_cash * (1 - CASH_RESERVE):
        # 现金不足，用剩余现金
        next_shares = int(available_cash * (1 - CASH_RESERVE) / price / 100) * 100
        if next_shares < 100:
            print(f'  ⏭  SKIP TOPUP: {symbol} 现金不足')
            return False, available_cash, pos_value
        cost = next_shares * price

    if next_shares <= 0:
        return False, available_cash, pos_value

    # 更新持仓
    old_cost = pos['entry_price'] * pos['shares']
    new_total_shares = pos['shares'] + next_shares
    pos['entry_price'] = round((old_cost + cost) / new_total_shares, 4)
    pos['shares'] = new_total_shares

    # 更新止损价（基于新均价）
    pos['stop_fixed'] = round(pos['entry_price'] * (1 + STOP_FIXED), 2)

    # 更新分批状态
    state['filled_shares'] += next_shares
    state['remaining_shares'] -= next_shares
    state['tranches_filled'] += 1
    state['last_fill_date'] = today
    state['avg_price'] = pos['entry_price']

    # 更新现金
    portfolio['cash'] -= cost
    available_cash -= cost
    pos_value += cost

    # 记录交易
    portfolio['trades'].append({
        'date': today, 'symbol': symbol, 'action': 'TOPUP',
        'price': price, 'shares': next_shares, 'cost': round(cost, 2),
        'fusion_level': pos.get('fusion_level', ''),
        'fusion_score': pos.get('fusion_score', 0),
        'reason': f'分批加仓 {state["tranches_filled"]}/{state["total_tranches"]}',
    })

    tranche_str = f'{state["tranches_filled"]}/{state["total_tranches"]}'
    remaining = state['remaining_shares']
    print(f'  [+] TOPUP: {symbol:<10} @ {price:.2f} x {next_shares} = {cost:,.0f}  '
          f'[批次 {tranche_str} 剩余 {remaining}股]')

    # 如果全部建完，清理 staged_entries
    if state['remaining_shares'] <= 0:
        print(f'  ✅ {symbol} 分批建仓完成 ({state["total_tranches"]}批)')

    return True, available_cash, pos_value

# === 信号生成 =============================================
def fetch_and_analyze(symbol, market='HK'):
    """使用 FusionController 进行三驾马车融合分析（XMM 60% + VP 25% + LLM 15%）"""
    global fc, market_state, vix_map

    result = fc.analyze_ticker(
        ticker=symbol,
        market_state=market_state,
        vix_regime=market_report.get('vix_regime', 'CANDIDATE') if market_report else 'CANDIDATE',
        vix_data=vix_map if vix_map else None,
    )

    if result.get('errors'):
        for e in result['errors']:
            print(f'    [ERROR] {e}')
        return None

    fusion = result.get('fusion', {})
    gate = result.get('gate', {})
    sources = result.get('sources', {})
    xmm_s = sources.get('xmm', {})
    llm_s = sources.get('llm', {})

    gate_rejected = not gate.get('approved', True)
    gate_reasons = gate.get('reject_reasons', [])
    gate_action = gate.get('action', fusion.get('level', 'HOLD'))

    # 高亮熔断日志
    if gate_rejected or (fusion.get('level') == 'HOLD' and gate_reasons):
        reasons_str = ' | '.join(gate_reasons) if gate_reasons else 'HardGate 熔断'
        print(f'\n  ╔══ [熔断拦截] ═══════════════════════')
        print(f'  ║ Ticker:  {symbol}')
        print(f'  ║ 原因:    {reasons_str}')
        print(f'  ║ 得分:    {fusion.get("score", 0):+.1f}')
        print(f'  ╚═══════════════════════════════════════')

    close_price = result.get('close', 0.0)
    closes = result.get('_closes', [])
    closes_arr = np.array(closes, dtype=float) if closes else np.array([close_price])

    # 计算 RSI（供报告显示）
    rsi_d = float(_rsi(closes_arr, 14)[-1]) if len(closes_arr) >= 15 else 50.0
    rsi_w = _weekly_rsi(closes_arr)
    rsi_w = float(rsi_w) if rsi_w is not None else 50.0

    # 止损参考价
    stop_fixed = round(close_price * (1 + (-0.12)), 2)

    return {
        'symbol': symbol,
        'market': market,
        'date': result.get('date', ''),
        'close': close_price,
        'data_source': result.get('data_source', 'Futu'),
        'fusion_level': gate_action if gate_rejected else fusion.get('level', 'HOLD'),
        'fusion_score': fusion.get('score', 0),
        'fusion_confidence': fusion.get('confidence', 0),
        'target_position': gate.get('adjusted_position', fusion.get('position_pct', 0)),
        'risk': fusion.get('risk_level', 'MEDIUM'),
        'warnings': result.get('warnings', []) + fusion.get('warnings', []),
        'reasoning': fusion.get('reasoning', ''),
        'fm_signal': fusion.get('level', 'HOLD'),
        'fm_score': fusion.get('score', 0),
        'xmm_signal': xmm_s.get('action', 'HOLD'),
        'xmm_confidence': xmm_s.get('position_size', 0.5),
        'rsi_daily': round(rsi_d, 1),
        'rsi_weekly': round(rsi_w, 1),
        'rsi_monthly': 50.0,
        'resonance': False,
        'td9_count': xmm_s.get('td_count', 0),
        'macd_desc': '',
        'fractal_type': 'NONE',
        'trend_up': False,
        'stop_levels': {
            'fixed': stop_fixed,
            'trailing_pct': STOP_TRAIL,
            'dd_limit': STOP_DD,
        },
        'llm_factor_score': round(llm_s.get('sentiment_score', 0.0), 1),
        'llm_factor_summary': llm_s.get('event_summary', ''),
        'llm_factor_details': {'sentiment': llm_s.get('sentiment_score', 0.0)},
        'event_sentiment_score': llm_s.get('sentiment_score', 0.0),
        'event_type': llm_s.get('event_type', 'none'),
        'event_summary': llm_s.get('event_summary', ''),
        'event_confidence': llm_s.get('confidence', 0.0),
        '_gate_approved': gate.get('approved', True),
        '_gate_reasons': gate_reasons,
    }

# === 三层架构接口适配 =========================================
def signal_dict_to_decision_signal(sig_dict: dict) -> DecisionSignal:
    """将 fetch_and_analyze 输出的 dict 转为标准 DecisionSignal dataclass"""
    return DecisionSignal(
        ticker=sig_dict.get('symbol', ''),
        direction=sig_dict.get('fm_signal', 'HOLD'),
        confidence=sig_dict.get('fusion_confidence', 0.0),
        date=sig_dict.get('date', ''),
        factor_score=sig_dict.get('fm_score', 0.0),
        factor_details=sig_dict.get('llm_factor_details', {}),
        llm_factor_score=sig_dict.get('llm_factor_score', 0.0),
        llm_factor_summary=sig_dict.get('llm_factor_summary', ''),
        # --- 情绪/事件层 ---
        event_sentiment_score=sig_dict.get('event_sentiment_score', 0.0),
        event_type=sig_dict.get('event_type', 'none'),
        event_summary=sig_dict.get('event_summary', ''),
        event_confidence=sig_dict.get('event_confidence', 0.0),
        fusion_score=sig_dict.get('fusion_score', 0.0),
        signal_level=sig_dict.get('fusion_level', 'HOLD'),
        regime=market_state,
        vix_regime=market_report.get('vix_regime', 'CANDIDATE') if market_report else 'CANDIDATE',
        risk_flags=[],
        warnings=sig_dict.get('warnings', []),
        llm_weight_used=15.0,  # 静态权重 15% (Base: LLM=15%)
        source=sig_dict.get('data_source', ''),
    )

# === 主流程 =================================================
today = datetime.now().strftime('%Y-%m-%d')
# 输出文件统一命名: {日期}_{市场}.json（与 auto_trade.py 的 check_signal_today 一致）
output_file = PT / 'signals' / f'{today}_HK.json'

print('=' * 60)
print(f'  纸交易信号生成 v2.0 {today}')
print('=' * 60)

# 加载已有持仓
portfolio_file = PT / 'portfolio.json'
if portfolio_file.exists():
    with open(portfolio_file, encoding='utf-8') as f:
        portfolio = json.load(f)
else:
    portfolio = {'cash': 100000, 'positions': [], 'trades': [], 'history': []}

# 检查已有持仓的止损（含ATR自适应）
new_positions = []
for pos in portfolio.get('positions', []):
    sym = pos['symbol']
    latest_price = None
    atr_pct = 0.03  # 默认值
    pos_source = '?'

    try:
        # 1. 尝试 Futu snapshot（无限流，与纸交易同源）
        futu_code = None
        parts = sym.split('.')
        if len(parts) == 2:
            futu_code = f'{parts[1]}.{parts[0]}'

        if FUTU_AVAILABLE and futu_code:
            try:
                quote_ctx = ft.OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
                try:
                    ret, snap = quote_ctx.get_market_snapshot([futu_code])
                    if ret == ft.RET_OK and snap is not None and len(snap) > 0:
                        latest_price = float(snap.iloc[0]['last_price'])
                        atr_pct = 0.03  # Futu snapshot 不含 ATR，用估算值
                        pos_source = 'Futu'
                finally:
                    quote_ctx.close()
            except:
                pass

        # 2. 回退 TickFlow（有 ATR 数据）
        if latest_price is None:
            q = tf.klines.get(sym, period='1d', count=20)
            if q and q.get('close'):
                latest_price = float(q['close'][-1])
                high = [float(h) for h in q['high']]
                low = [float(l) for l in q['low']]
                close = [float(c) for c in q['close']]
                tr = [max(high[0]-low[0], abs(high[0]-close[0]), abs(low[0]-close[0]))]
                for i in range(1, len(close)):
                    tr.append(max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1])))
                atr14 = sum(tr[-14:]) / 14 if len(tr) >= 14 else tr[-1]
                atr_pct = atr14 / latest_price if latest_price > 0 else 0.03
                pos_source = 'TickFlow'

        if latest_price is None:
            new_positions.append(pos)
            continue

        pnl = (latest_price - pos['entry_price']) / pos['entry_price']
        pos['highest'] = max(pos['highest'], latest_price)
        pos['current_price'] = latest_price
        pos['pnl'] = round(pnl * 100, 2)
        pos['atr_pct'] = round(atr_pct * 100, 2)
        pos['triggered_stop'] = False

        # 使用ATR自适应止损
        triggered, reason = check_stop_loss_adaptive(pos, latest_price, atr_pct)
        if triggered:
            pos['triggered_stop'] = True
            pos['stop_reason'] = reason

        if pos['triggered_stop']:
            portfolio['cash'] += pos['shares'] * latest_price
            portfolio['trades'].append({
                'date': today, 'symbol': sym, 'action': 'STOP',
                'price': latest_price, 'shares': pos['shares'],
                'pnl': round(pnl*100, 2), 'reason': pos['stop_reason']
            })
            print(f'  [!] STOP: {sym} @ {latest_price:.2f} ({pos["stop_reason"]}) [{pos_source}]')
        else:
            new_positions.append(pos)
            print(f'  [OK] HOLD: {sym} @ {latest_price:.2f} (PnL={pnl:+.1%}) [{pos_source}]')
    except Exception as e:
        new_positions.append(pos)  # 查价失败，保留持仓

portfolio['positions'] = new_positions

# 获取市场状态（必须在信号生成之前，FusionEngine 需要 market_state 做 regime-dependent LLM weight）
market_state = 'CRAB'  # 默认值（分类器失败时回退）
market_report = None
try:
    clf = MarketStateClassifier('SPY.US')
    clf.load_data()
    market_report = clf.analyze()
    market_state = market_report['market_state']
    print(f'\n  Market State: {market_state} (VIX: {market_report["vix_regime"]} | Trend: {market_report["trend"]} | Momentum: {market_report["momentum"]})')
except Exception as e:
    print(f'\n  [WARN] MarketStateClassifier 失败, 回退 CRAB: {e}')
    print(f'  Market State: {market_state}')

# 根据市场状态动态调整总仓位上限
ms_limit = MARKET_STATE_POSITION_LIMIT.get(market_state, 0.40)
if ms_limit < MAX_TOTAL_EXPOSURE:
    print(f'  ⚠ 总仓位上限: {MAX_TOTAL_EXPOSURE:.0%} → {ms_limit:.0%} (市场状态: {market_state})')
    MAX_TOTAL_EXPOSURE = ms_limit

# 生成新信号（FusionController 三驾马车：XMM 60% + VP 25% + LLM 15%）
all_signals = []
for market, symbols in TARGETS.items():
    print(f'\n--- {market} ({len(symbols)} stocks) ---')
    for sym in symbols:
        print(f'  Analyzing {sym}...', end=' ', flush=True)
        result = fetch_and_analyze(sym, market)
        if result:
            all_signals.append(result)
            short = result['fusion_level']
            src = result.get('data_source', '?')
            print(f'→ {short} ({result["fusion_score"]:+.1f}) [{src}]')
        else:
            print('→ FAILED')

# 按 fusion_score 排序
all_signals.sort(key=lambda x: x['fusion_score'], reverse=True)

# === 三层架构标注：研究层 → DecisionSignal ====================
print(f'\n  [架构] 研究层完成: {len(all_signals)} 条 DecisionSignal')
for sig in all_signals:
    ds = signal_dict_to_decision_signal(sig)
    sig['_decision_signal'] = {
        'ticker': ds.ticker,
        'direction': ds.direction,
        'confidence': ds.confidence,
        'fusion_score': ds.fusion_score,
        'signal_level': ds.signal_level,
        'regime': ds.regime,
        'vix_regime': ds.vix_regime,
        'llm_weight_used': ds.llm_weight_used,
        'risk_flags': ds.risk_flags,
        'layer': 'research → decision',
    }

# JSON安全转换函数
import numpy as _np
def _json_safe(val):
    if isinstance(val, (_np.integer,)): return int(val)
    if isinstance(val, (np.floating,)): return float(val)
    if isinstance(val, (_np.ndarray,)): return val.tolist()
    if isinstance(val, (_np.bool_,)): return bool(val)
    return val

def _make_json_safe(d):
    return {k: _json_safe(v) for k, v in d.items()}

# 生成安全版本用于保存
signals_safe = [_make_json_safe(s) for s in all_signals]

# === 信号反转自动清仓 =========================================
# 当持仓标的信号从 BUY 翻转为 SELL，自动清仓
SIGNAL_REVERSAL_CONFIG = {
    'enabled': True,
    'trigger_levels': ['SELL', 'STRONG_SELL'],
    'exclude_if_pnl_above': 0.05,  # 浮盈超过5%不自动清仓（让利润跑）
}

print(f'\n{"="*60}')
print(f'  信号反转清仓检查')
print(f'{"="*60}')

all_signals_map = {s['symbol']: s for s in all_signals}
positions_to_remove = []

for pos in portfolio['positions']:
    sym = pos['symbol']
    sig = all_signals_map.get(sym)

    if not sig:
        continue

    current_level = sig['fusion_level']
    current_pnl = pos.get('pnl', 0) / 100

    if current_level in SIGNAL_REVERSAL_CONFIG['trigger_levels']:
        if current_pnl > SIGNAL_REVERSAL_CONFIG['exclude_if_pnl_above']:
            print(f'  ⏸  HOLD: {sym} 信号{current_level}但浮盈{current_pnl:+.1%}，让利润跑')
            continue

        latest_price = pos.get('current_price', pos['entry_price'])
        pnl_pct = (latest_price / pos['entry_price'] - 1) * 100

        portfolio['cash'] += pos['shares'] * latest_price
        portfolio['trades'].append({
            'date': today, 'symbol': sym, 'action': 'SIGNAL_EXIT',
            'price': latest_price, 'shares': pos['shares'],
            'cost': round(pos['shares'] * latest_price, 2),
            'fusion_level': current_level,
            'fusion_score': sig['fusion_score'],
            'pnl': round(pnl_pct, 2),
            'reason': f'信号反转清仓: {current_level} (score={sig["fusion_score"]:+.1f})',
        })
        positions_to_remove.append(sym)
        print(f'  [!] SIGNAL_EXIT: {sym} @ {latest_price:.2f} (PnL={pnl_pct:+.1f}%) 信号→{current_level}')

if positions_to_remove:
    portfolio['positions'] = [p for p in portfolio['positions'] if p['symbol'] not in positions_to_remove]
    staged = get_staged_state(portfolio)
    for sym in positions_to_remove:
        if sym in staged:
            del staged[sym]
    print(f'  已清仓: {", ".join(positions_to_remove)}')
    print(f'  剩余现金: {portfolio["cash"]:,.0f}')
else:
    print('  → 无信号反转触发清仓')

# === 纸交易自动建仓 =========================================
# 当前已持有代码
held = {p['symbol'] for p in portfolio['positions']}
pos_value = sum(p['shares'] * p['current_price']
                for p in portfolio['positions']
                if 'current_price' in p)
total_value = portfolio['cash'] + pos_value

# 如果 portfolio 为空（已迁移至 Futu），从 Futu 查询真实账户状态
if total_value <= 0:
    try:
        _ft_ctx = ft.OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
        _ft_trd = __import__('futu').OpenSecTradeContext(filter_trdmarket='HK', host=FUTU_HOST, port=FUTU_PORT)
        ret, acc = _ft_trd.accinfo_query(trd_env='SIMULATE')
        if ret == 0:
            total_value = float(acc.iloc[0].get('total_assets', 0))
            portfolio['cash'] = float(acc.iloc[0].get('cash', 0))
            pos_value = float(acc.iloc[0].get('market_val', 0))
        ret2, pdata = _ft_trd.position_list_query(trd_env='SIMULATE')
        if ret2 == 0 and pdata is not None and len(pdata) > 0:
            for _, r in pdata.iterrows():
                _code = r['code']
                _qty = float(r.get('qty', 0))
                if _qty > 0:
                    _sym = _code.replace('HK.', '') + '.HK'
                    held.add(_sym)
        _ft_trd.close()
        _ft_ctx.close()
        print(f'  [Futu] 账户同步: 总资产 {total_value:,.0f} | 现金 {portfolio["cash"]:,.0f} | 持仓 {pos_value:,.0f}')
    except Exception as e:
        print(f'  [WARN] Futu 账户同步失败: {e}')

# 分类信号
buyable = [s for s in all_signals
           if s['symbol'] not in held
           and s['fusion_score'] >= MIN_ENTRY_SCORE
           and s['fusion_level'] in ('STRONG_BUY', 'BUY')]
strong_buys = [s for s in buyable if s['fusion_level'] == 'STRONG_BUY']
regular_buys = [s for s in buyable if s['fusion_level'] == 'BUY']

print(f'\n{"="*60}')
print(f'  自动建仓决策')
print(f'{"="*60}')
exposure_pct = pos_value / total_value if total_value > 0 else 0
print(f'  Cash: {portfolio["cash"]:,.0f}  Pos Value: {pos_value:,.0f}  Total: {total_value:,.0f}')
print(f'  Positions: {len(portfolio["positions"])}/{MAX_POSITIONS}  Exposure: {exposure_pct:.1%}')
print(f'  Market State: {market_state}')
print(f'  STRONG_BUY: {len(strong_buys)}  BUY: {len(regular_buys)}')

allocated = []

def try_buy(sig, available_cash, pos_value, total_value, market_state):
    """尝试建仓一只，支持分批建仓。返回 (成功bool, 更新后available_cash, 更新后pos_value)"""
    # 硬门槛检查（HardGate — 决策层纯规则，零LLM）
    vix_regime = market_report.get('vix_regime', 'CANDIDATE') if market_report else 'CANDIDATE'
    gate = hard_gate.check(
        signal=sig,
        market_state=market_state,
        vix_regime=vix_regime,
        current_positions=portfolio['positions'],
        total_value=total_value,
    )
    if not gate.approved:
        print(f'  🚫 GATE BLOCK: {sig["symbol"]:<10} {" | ".join(gate.reject_reasons)}')
        return False, available_cash, pos_value
    if gate.warnings:
        print(f'  ⚠  GATE WARN: {sig["symbol"]:<10} {" | ".join(gate.warnings)}')

    # === 决策层 → OrderDirective ===
    directive = OrderDirective(
        ticker=sig['symbol'],
        action=sig['fusion_level'] if gate.approved else 'BLOCKED',
        approved=gate.approved,
        reject_reasons=gate.reject_reasons,
        target_position_pct=gate.adjusted_position_pct,
        stop_loss_pct=sig['stop_levels']['fixed'] / sig['close'] - 1 if sig['close'] > 0 else -0.12,
        trailing_stop_pct=sig['stop_levels']['trailing_pct'],
        decision_timestamp=today,
        confidence=sig['fusion_confidence'],
        regime=market_state,
        warnings=gate.warnings,
    )
    sig['_order_directive'] = {
        'action': directive.action,
        'approved': directive.approved,
        'target_position_pct': directive.target_position_pct,
        'reject_reasons': directive.reject_reasons,
        'layer': 'decision → execution',
    }

    # 信号质量过滤（原有逻辑保留，作为二级检查）
    passed, reason = filter_signal(sig, market_state)
    if not passed:
        print(f'  ⏭  SKIP: {sig["symbol"]:<10} {reason}')
        return False, available_cash, pos_value
    
    if len(portfolio['positions']) >= MAX_POSITIONS:
        return False, available_cash, pos_value
    if total_value > 0 and pos_value / total_value >= MAX_TOTAL_EXPOSURE:
        return False, available_cash, pos_value
    price = sig['close']
    # 目标：STRONG_BUY 固定比例；BUY 按分数权重（降低系数防止超配）
    if sig['fusion_level'] == 'STRONG_BUY':
        target = total_value * STRONG_BUY_ALLOCATION
    else:
        total_score = sum(s['fusion_score'] for s in regular_buys)
        weight = sig['fusion_score'] / total_score if total_score else 0
        target = total_value * sig['target_position'] * weight * 0.4
    target = min(target, available_cash * (1 - CASH_RESERVE))
    # 最低门槛：至少能买1手
    if target < price * MIN_SHARES:
        target = int(available_cash * 0.5 / price / 100) * 100 * price  # 降级：用50%现金整手
        if target < price * MIN_SHARES:
            print(f'  ⏭  SKIP: {sig["symbol"]:<10} 目标 {target:.0f} < 最低门槛 {price*MIN_SHARES:.0f}')
            return False, available_cash, pos_value
    total_shares = int(target / price / 100) * 100
    if total_shares < MIN_SHARES:
        total_shares = int(available_cash * (1 - CASH_RESERVE) / price / 100) * 100
        if total_shares < MIN_SHARES:
            print(f'  ⏭  SKIP: {sig["symbol"]:<10} 整手后股数不足')
            return False, available_cash, pos_value

    # 分批建仓逻辑
    if STAGED_ENTRY_CONFIG['enabled']:
        # 首批只建 1/3 仓位
        first_shares = register_staged_entry(
            portfolio, sig['symbol'], total_shares,
            total_shares * price, STAGED_ENTRY_CONFIG['first_pct']
        )
        shares = first_shares
        staged_info = f' [分批 1/{STAGED_ENTRY_CONFIG["total_tranches"]} 总目标{total_shares}股]'
    else:
        shares = total_shares
        staged_info = ''

    cost = shares * price
    new_pos = {
        'symbol':       sig['symbol'], 'market': sig['market'],
        'entry_price':  price, 'shares': shares, 'entry_date': today,
        'highest':      price,
        'stop_fixed':   sig['stop_levels']['fixed'],
        'stop_trail':   sig['stop_levels']['trailing_pct'],
        'stop_dd':      sig['stop_levels']['dd_limit'],
        'fusion_level': sig['fusion_level'], 'fusion_score': sig['fusion_score'],
        'stop_reason': '', 'triggered_stop': False,
    }
    portfolio['positions'].append(new_pos)
    portfolio['cash'] -= cost
    available_cash -= cost
    pos_value += cost
    portfolio['trades'].append({
        'date': today, 'symbol': sig['symbol'], 'action': 'BUY', 'price': price,
        'shares': shares, 'cost': round(cost, 2),
        'fusion_level': sig['fusion_level'], 'fusion_score': sig['fusion_score'],
        'reason': sig['reasoning'][:80],
    })
    allocated.append(sig['symbol'])
    print(f'  [+] BUY: {sig["symbol"]:<10} @ {price:.2f} x {shares} = {cost:,.0f}  '
          f'[{sig["fusion_level"]} score={sig["fusion_score"]:+.0f}]{staged_info}')
    return True, available_cash, pos_value

if not buyable:
    print('  → 无可建仓信号，跳过')
else:
    available_cash = portfolio['cash']
    # 第一轮：STRONG_BUY 固定比例分配
    for sig in strong_buys:
        _, available_cash, pos_value = try_buy(sig, available_cash, pos_value, total_value, market_state)
    # 第二轮：BUY 分数权重分配（剩余现金）
    if regular_buys and len(portfolio['positions']) < MAX_POSITIONS:
        for sig in regular_buys:
            if len(portfolio['positions']) >= MAX_POSITIONS:
                print(f'  → 持仓数达上限')
                break
            _, available_cash, pos_value = try_buy(sig, available_cash, pos_value, total_value, market_state)

    if allocated:
        print(f'\n  新建仓位: {", ".join(allocated)}')
        print(f'  剩余现金: {portfolio["cash"]:,.0f}')
    else:
        print('  → 无合适建仓机会')

# === 分批加仓检查 =============================================
if STAGED_ENTRY_CONFIG['enabled']:
    print(f'\n{"="*60}')
    print(f'  分批加仓检查')
    print(f'{"="*60}')

    all_signals_map = {s['symbol']: s for s in all_signals}
    topup_list = check_staged_topup(portfolio, all_signals_map)

    if topup_list:
        available_cash = portfolio['cash']
        for item in topup_list:
            _, available_cash, pos_value = execute_topup(
                portfolio, item, available_cash, pos_value, total_value
            )
        print(f'  剩余现金: {portfolio["cash"]:,.0f}')
    else:
        print('  → 无需加仓的分批计划')

    # 显示所有分批状态
    staged = get_staged_state(portfolio)
    if staged:
        print(f'\n  分批建仓状态:')
        for sym, st in staged.items():
            status = '✅完成' if st['remaining_shares'] <= 0 else f'{st["tranches_filled"]}/{st["total_tranches"]}'
            print(f'    {sym:<10} {status}  已建{st["filled_shares"]}股  剩余{st["remaining_shares"]}股')

print()

# 建仓后重新生成快照（确保最新现金/持仓状态）
portfolio_safe = _make_json_safe(portfolio)

# === 保存 ====================================================

report = {
    'date': today,
    'generated_at': datetime.now().isoformat(),
    'vix_loaded': len(vix_map) > 0,
    'market_state': market_report if 'market_report' in globals() else None,
    'architecture': {
        'version': '3.0-fusion-controller',
        'research_layer': {
            'modules': ['core/fusion_controller.py (XMM + VP + LLM 三驾马车)'],
            'output_type': 'DecisionSignal',
            'llm_allowed': True,
            'llm_calls_runtime': 0,
            'weights': 'XMM 60% | VP 25% | LLM 15% (静态硬编码)',
        },
        'decision_layer': {
            'modules': ['fusion_framework/fusion_engine.py (FusionEngine.fuse)', 'fusion_framework/hard_gate.py'],
            'output_type': 'OrderDirective',
            'llm_allowed': False,
            'weights': '异常源断流→存活源重新归一化',
            'features': ['regime-dependent-llm-weight', 'hard-gate-checks', 'market-state-aware'],
        },
        'execution_layer': {
            'modules': ['paper_trading/futu_bridge.py', 'futu_api'],
            'output_type': 'Order',
            'llm_allowed': False,
            'features': ['stop-loss', 'trailing-stop', 'staged-entry'],
        },
    },
    'signals': signals_safe,
    'portfolio': portfolio_safe,
    'summary': {
        'total': len(all_signals),
        'strong_buy': sum(1 for s in all_signals if s['fusion_level']=='STRONG_BUY'),
        'buy': sum(1 for s in all_signals if s['fusion_level']=='BUY'),
        'sell': sum(1 for s in all_signals if s['fusion_level'] in ('STRONG_SELL','SELL')),
        'reduced': sum(1 for s in all_signals if s['fusion_level']=='REDUCED'),
        'hold': sum(1 for s in all_signals if s['fusion_level']=='HOLD'),
    }
}

with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
with open(portfolio_file, 'w', encoding='utf-8') as f:
    json.dump(portfolio_safe, f, ensure_ascii=False, indent=2)

# === 写入 SQLite（并行存储，不影响 JSON）===
try:
    from core.quant_db import QuantDB
    db = QuantDB()
    # 写入市场状态
    if report.get('market_state'):
        db.insert_market_state(today, report['market_state'])
    # 写入信号（按市场分组）
    hk_signals = [s for s in all_signals if s.get('market') == 'HK']
    us_signals = [s for s in all_signals if s.get('market') in ('US', 'SPX')]
    if hk_signals:
        db.insert_signals(today, hk_signals, market='HK')
    if us_signals:
        db.insert_signals(today, us_signals, market='US')
    # 写入交易记录
    today_trades = [t for t in portfolio.get('trades', []) if t.get('date') == today]
    if today_trades:
        db.insert_trades(today, today_trades)
    # 写入持仓快照
    if portfolio.get('positions'):
        db.insert_positions(today, portfolio['positions'])
    db_stats = db.stats()
    db.close()
    print(f'  💾 已写入 quant.db (signals={db_stats["signals"]}, trades={db_stats["trades"]})')
except Exception as e:
    print(f'  [WARN] SQLite 写入失败: {e}')

# 打印摘要
print(f'\n{"="*60}')
print(f'  信号摘要 ({len(all_signals)} stocks)')
print(f'{"="*60}')
s = report['summary']
print(f'  STRONG: {s["strong_buy"]}  BUY: {s["buy"]}  HOLD: {s["hold"]}  SELL: {s["sell"]}')
print(f'  VIX: {"loaded" if report["vix_loaded"] else "NOT LOADED"}')
if report['market_state']:
    print(f'  Market: {report["market_state"]["market_state"]} | VIX: {report["market_state"]["vix_regime"]}')

print(f'\n  ** TOP BUY **')
for sig in all_signals:
    if sig['fusion_level'] in ('STRONG_BUY','BUY'):
        print(f'    {sig["symbol"]:<10} {sig["fusion_level"]:<12} '
              f'score={sig["fusion_score"]:+.1f} conf={sig["fusion_confidence"]:.0%} '
              f'pos={sig["target_position"]:.0%} rsi_d={sig["rsi_daily"]}')
        if sig['warnings']:
            print(f'      WARN: {"; ".join(sig["warnings"])}')

print(f'\n  ** SELL **')
for sig in all_signals:
    if sig['fusion_level'] in ('STRONG_SELL','SELL'):
        print(f'    {sig["symbol"]:<10} {sig["fusion_level"]:<12} '
              f'score={sig["fusion_score"]:+.1f} risk={sig["risk"]}')

print(f'\n  Saved: {output_file}')
print(f'  Portfolio: {portfolio_file}')

# === 日报生成（P0-2）========================================
def generate_daily_report(report_data, portfolio_data, signals):
    """生成Markdown日报"""
    from pathlib import Path
    report_dir = PT / 'reports'
    report_dir.mkdir(exist_ok=True)

    date_str = report_data['date']
    report_file = report_dir / f'{date_str}.md'

    # 统计交易
    today_trades = [t for t in portfolio_data.get('trades', []) if t['date'] == date_str]
    buy_trades = [t for t in today_trades if t['action'] == 'BUY']
    sell_trades = [t for t in today_trades if t['action'] in ('SELL', 'STOP', 'SIGNAL_EXIT')]
    topup_trades = [t for t in today_trades if t['action'] == 'TOPUP']

    # 计算持仓市值
    pos_value = sum(p.get('shares', 0) * p.get('current_price', p.get('entry_price', 0))
                   for p in portfolio_data.get('positions', []))
    cash = portfolio_data.get('cash', 0)
    total_value = cash + pos_value

    # 计算日PnL
    daily_pnl = sum(t.get('pnl', 0) for t in today_trades)

    # 市场状态
    ms = report_data.get('market_state', {})
    market_state = ms.get('market_state', 'CRAB') if ms else 'CRAB'
    vix_regime = ms.get('vix_regime', 'CANDIDATE') if ms else 'CANDIDATE'

    # 生成Markdown
    md = f"""# 纸交易日报 - {date_str}

## 📊 账户概览

| 指标 | 数值 |
|------|------|
| **总资产** | {total_value:,.0f} |
| **现金** | {cash:,.0f} ({cash/total_value:.1%}) |
| **持仓市值** | {pos_value:,.0f} ({pos_value/total_value:.1%}) |
| **持仓数** | {len(portfolio_data.get('positions', []))}/{MAX_POSITIONS} |
| **日交易** | {len(today_trades)}笔 |
| **日PnL** | {daily_pnl:+.2f}% |

## 🎯 市场状态

- **市场状态**: {market_state}
- **VIX环境**: {vix_regime}
- **趋势**: {ms.get('trend', 'N/A') if ms else 'N/A'}
- **动量**: {ms.get('momentum', 'N/A') if ms else 'N/A'}

## 📈 信号汇总

| 信号类型 | 数量 |
|---------|------|
| STRONG_BUY | {report_data['summary']['strong_buy']} |
| BUY | {report_data['summary']['buy']} |
| HOLD | {report_data['summary']['hold']} |
| SELL | {report_data['summary']['sell']} |
| REDUCED | {report_data['summary']['reduced']} |

"""
    md += "\n"

    # TOP BUY信号
    buy_signals = [s for s in signals if s['fusion_level'] in ('STRONG_BUY', 'BUY')]
    if buy_signals:
        md += "## 🟢 TOP BUY 信号\n\n"
        md += "| 标的 | 信号 | 分数 | 置信度 | RSI日 | 建议仓位 |\n"
        md += "|------|------|------|--------|-------|----------|\n"
        for s in buy_signals[:5]:
            md += f"| {s['symbol']} | {s['fusion_level']} | {s['fusion_score']:+.1f} | {s['fusion_confidence']:.0%} | {s['rsi_daily']:.1f} | {s['target_position']:.0%} |\n"
        md += "\n"

    # SELL信号
    sell_signals = [s for s in signals if s['fusion_level'] in ('STRONG_SELL', 'SELL')]
    if sell_signals:
        md += "## 🔴 SELL 信号\n\n"
        md += "| 标的 | 信号 | 分数 | 风险等级 |\n"
        md += "|------|------|------|----------|\n"
        for s in sell_signals:
            md += f"| {s['symbol']} | {s['fusion_level']} | {s['fusion_score']:+.1f} | {s['risk']} |\n"
        md += "\n"

    # 持仓明细
    if portfolio_data.get('positions'):
        md += "## 💼 持仓明细\n\n"
        md += "| 标的 | 股数 | 成本价 | 现价 | PnL | 止损价 |\n"
        md += "|------|------|--------|------|-----|--------|\n"
        for p in portfolio_data['positions']:
            pnl = p.get('pnl', 0)
            pnl_color = "🟢" if pnl >= 0 else "🔴"
            md += f"| {p['symbol']} | {p['shares']} | {p['entry_price']:.2f} | {p.get('current_price', p['entry_price']):.2f} | {pnl_color} {pnl:+.2f}% | {p.get('stop_fixed', 0):.2f} |\n"
        md += "\n"

    # 今日交易
    if today_trades:
        md += "## 📝 今日交易\n\n"
        for t in today_trades:
            pnl_str = f" (PnL: {t.get('pnl', 0):+.2f}%)" if 'pnl' in t else ""
            md += f"- **{t['action']}** {t['symbol']} @ {t['price']:.2f} x {t['shares']}股 = {t.get('cost', t['price']*t['shares']):,.0f}{pnl_str}\n"
            if t.get('reason'):
                md += f"  - {t['reason']}\n"
        md += "\n"

    # 分批建仓状态
    staged = portfolio_data.get('staged_entries', {})
    if staged:
        md += "## 🔄 分批建仓进度\n\n"
        md += "| 标的 | 已建批次 | 已建股数 | 剩余股数 |\n"
        md += "|------|---------|---------|----------|\n"
        for sym, st in staged.items():
            if st['remaining_shares'] > 0:
                md += f"| {sym} | {st['tranches_filled']}/{st['total_tranches']} | {st['filled_shares']} | {st['remaining_shares']} |\n"
        md += "\n"

    # 风控检查
    md += "## ⚠️ 风控检查\n\n"
    risk_issues = []
    if pos_value / total_value > RISK_CONFIG['max_total_position']:
        risk_issues.append(f"总仓位过高: {pos_value/total_value:.1%} > {RISK_CONFIG['max_total_position']:.0%}")
    for p in portfolio_data.get('positions', []):
        if p.get('triggered_stop'):
            risk_issues.append(f"{p['symbol']} 触发止损: {p.get('stop_reason', 'N/A')}")

    if risk_issues:
        for issue in risk_issues:
            md += f"- ❌ {issue}\n"
    else:
        md += "- ✅ 无风控触发\n"
    md += "\n"

    # 明日建议
    md += "## 💡 明日建议\n\n"
    if market_state in ('BEAR', 'CORRECTION'):
        md += "- 🔴 **防守优先**: 市场环境不利，严格控制仓位，关注止损\n"
    elif market_state == 'CRAB':
        md += "- 🟡 **中性观望**: 震荡市场，精选信号，分批建仓\n"
    else:
        md += "- 🟢 **积极布局**: 市场向好，关注STRONG_BUY信号\n"

    if buy_signals:
        md += f"- 关注: {', '.join([s['symbol'] for s in buy_signals[:3]])}\n"
    if sell_signals:
        md += f"- 回避: {', '.join([s['symbol'] for s in sell_signals])}\n"

    md += f"\n---\n*生成时间: {report_data['generated_at']}*\n"

    # 保存日报
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(md)

    print(f'\n  📄 日报已生成: {report_file}')
    return report_file

# 生成日报
try:
    generate_daily_report(report, portfolio_safe, all_signals)
except Exception as e:
    print(f'  [WARN] 日报生成失败: {e}')

# === 保存战术仪表板 ============================================
try:
    vix_regime = market_report.get('vix_regime', 'N/A') if market_report else 'N/A'
    dashboard = fc.render_tactical_dashboard(
        signals=all_signals,
        market_state=market_state,
        vix_regime=vix_regime,
        date=today,
    )
    dashboard_file = PT / 'last_scan_report.txt'
    with open(dashboard_file, 'w', encoding='utf-8') as f:
        f.write(dashboard + '\n')
    print(f'  📊 战术仪表板已保存: {dashboard_file}')
except Exception as e:
    print(f'  [WARN] 战术仪表板生成失败: {e}')
    import traceback
    traceback.print_exc()
