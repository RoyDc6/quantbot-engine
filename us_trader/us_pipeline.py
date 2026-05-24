# -*- coding: utf-8 -*-
"""
US Stock Simulated Trading Pipeline v2.0
升级: 3指标 → FusionEngine(缠论+XMM+LLM因子+VIX风控)
对齐港股管线架构，Futu为唯一持仓真相源
"""
import sys, io, os, json, time, warnings
from pathlib import Path
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

BASE = Path('E:/quant')
PT = BASE / 'paper_trading'
FW = BASE / 'fusion_framework'
SKILL = Path('C:/Users/RoyGoode/.workbuddy/skills/xmm-strategy/scripts')

sys.path.insert(0, str(BASE))
sys.path.insert(0, str(FW))
sys.path.insert(0, str(SKILL))

from chan.fractal import mark_fractals
from xmm_signals import XMMSignalEngine
from fusion_engine import FusionEngine as FrameworkEngine
from signal_types import FusionModelSignal, XMMSignal
from llm_factor_factory.factor_scorer import score_factors, factor_to_signal_score, get_factor_summary

# Futu API
import futu as ft

FUTU_HOST = '127.0.0.1'
FUTU_PORT = 11111
TRD_ENV = ft.TrdEnv.SIMULATE

BASE_DIR = BASE / 'us_trader'
OUTPUT_DIR = BASE_DIR / 'output'
STATE_DIR = BASE_DIR / 'state'
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)

# ─── Universe (15 mega-cap + sector diversification) ───
US_UNIVERSE = [
    'US.AAPL', 'US.AMZN', 'US.MSFT', 'US.GOOGL', 'US.META',
    'US.NVDA', 'US.TSLA', 'US.AMD', 'US.AVGO', 'US.ORCL',
    'US.NFLX', 'US.CRM', 'US.ADBE', 'US.INTC', 'US.QCOM',
]

# ─── Risk Config (对齐港股) ───
MAX_POSITION_PCT = 0.20
MAX_TOTAL_PCT = 0.80
FIXED_STOP_PCT = -0.12          # 对齐港股 -12%
TRAILING_STOP_PCT = -0.12
ATR_LOW_VOL_THRESH = 0.025
ATR_LOW_STOP_PCT = -0.15
PORTFOLIO_DD_PCT = -0.30
STOP_COOLDOWN_DAYS = 10

# 分批建仓（对齐港股）
STAGED_ENTRY_CONFIG = {
    'enabled': True,
    'total_tranches': 3,
    'first_pct': 0.34,
    'min_gap_days': 1,
}

# 信号反转清仓（对齐港股）
SIGNAL_REVERSAL_CONFIG = {
    'enabled': True,
    'trigger_levels': ['SELL', 'STRONG_SELL'],
    'exclude_if_pnl_above': 0.05,
}

# ─── 辅助函数 ───────────────────────────────────────────
def rsi(close, n=14):
    d = np.diff(close); g = np.where(d>0,d,0.0); lo = np.where(d<0,-d,0.0)
    r = np.full(len(close), 50.0)
    if len(close) <= n: return r
    ag, al = np.mean(g[:n]), np.mean(lo[:n])
    r[n] = 50.0 if al<1e-10 else 100-100/(1+ag/(al+1e-10))
    for i in range(n+1,len(close)):
        ag = (ag*(n-1)+g[i-1])/n; al = (al*(n-1)+lo[i-1])/n
        r[i] = 50.0 if al<1e-10 else 100-100/(1+ag/(al+1e-10))
    return r

def weekly_rsi(close, p=4):
    n = len(close)
    if n < 30: return 50.0
    w = [close[i] for i in range(4, n, 5)]
    if len(w) < p+1: return 50.0
    return float(rsi(np.array(w,dtype=float), p)[-1])

# ─── 初始化引擎 ─────────────────────────────────────────
fw_engine = FrameworkEngine()
xmm_engine = XMMSignalEngine()

# 加载 VIX 数据
vxx_path = BASE / 'scanner' / 'cache' / 'VXX_US.json'
vix_map = {}
if vxx_path.exists():
    with open(vxx_path) as f:
        vxx = json.load(f)
    for rec in vxx:
        d = datetime.fromtimestamp(rec['date']/1000).strftime('%Y-%m-%d')
        vix_map[d] = float(rec['close'])
    fw_engine.set_vix_data(vix_map)

# ─── 信号生成（复用 FusionEngine）────────────────────────
def fetch_and_analyze_us(code):
    """美股信号生成 — 与港股 daily_runner 同架构"""
    # 动态日期范围
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')

    try:
        ctx = ft.OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
        try:
            ret, df, _ = ctx.request_history_kline(
                code=code, start=start_date, end=end_date,
                ktype=ft.KLType.K_DAY, autype=ft.AuType.QFQ)
            if ret != ft.RET_OK or df is None or len(df) < 60:
                return None
        finally:
            ctx.close()
    except Exception as e:
        print(f'  [ERROR] {code}: fetch failed - {e}')
        return None

    df.columns = [c.lower() for c in df.columns]
    close = df['close'].values.astype(float)
    date = str(df.iloc[-1].get('time_key', ''))[:10]
    last_close = float(close[-1])
    symbol = code.replace('US.', '') + '.US'  # AAPL.US

    # 缠论分型
    df2 = mark_fractals(df.copy())
    top_cnt = int((df2['fractal']==1).sum())
    bot_cnt = int((df2['fractal']==-1).sum())
    lt = df2[df2['fractal']==1].tail(1)
    lb = df2[df2['fractal']==-1].tail(1)

    if len(lt) and len(lb):
        ftype = 'TOP' if lt.index[-1] > lb.index[-1] else 'BOTTOM'
    elif len(lt): ftype = 'TOP'
    elif len(lb): ftype = 'BOTTOM'
    else: ftype = 'NONE'

    # 融合模型信号
    w_rsi = weekly_rsi(close, 4)
    m_rsi = weekly_rsi(close, 20)
    rsi_d = rsi(close, 14)

    sma50 = np.full(len(close), np.nan)
    for i in range(49, len(close)):
        sma50[i] = sma50[i-1] + (close[i] - sma50[i-1]) / 50
    trend_up = close[-1] > sma50[-1] if not np.isnan(sma50[-1]) else False

    if ftype == 'BOTTOM':
        score, sig, conf = bot_cnt*2.0, 'BUY', min(0.88, 0.5+bot_cnt*0.05)
    elif ftype == 'TOP':
        score, sig, conf = -top_cnt*2.0, 'SELL', min(0.88, 0.5+top_cnt*0.05)
    else:
        score, sig, conf = 0.0, 'HOLD', 0.50

    fm_sig = FusionModelSignal(symbol, date, float(score), float(w_rsi),
                                float(m_rsi), 0.0, sig, float(conf),
                                {'trend_up': trend_up})

    # XMM 信号
    xmm_result = xmm_engine.analyze(df)
    xmm_action = xmm_result['signal']
    xmm_conf = {3:0.85, 2:0.72, 1:0.60, 0:0.50}.get(xmm_result['strength'], 0.50)

    sma20 = np.full(len(close), np.nan)
    for i in range(19, len(close)):
        sma20[i] = sma20[i-1] + (close[i] - sma20[i-1]) / 20
    sma_pos = float(close[-1]/sma20[-1]) if not np.isnan(sma20[-1]) else 1.0

    xmm_sig = XMMSignal(symbol, date, xmm_action, xmm_conf,
                       float(rsi_d[-1]), sma_pos,
                       float(xmm_result.get('td9_count', 0)),
                       str(xmm_result.get('reason','')), '',
                       str(xmm_result.get('macd_desc','')))

    # LLM 因子打分
    llm_factor_score = 0.0
    llm_factor_summary = ''
    try:
        market_label = code.replace('US.', '')  # AAPL
        llm_factor_scores = score_factors(df, market_label)
        llm_factor_score = factor_to_signal_score(llm_factor_scores)
        llm_factor_summary = get_factor_summary(llm_factor_scores)
    except Exception as e:
        pass

    # 框架融合
    fusion = fw_engine.fuse(fm_sig, xmm_sig, 'US', llm_factor_score, llm_factor_summary)

    return {
        'code': code,
        'symbol': symbol,
        'market': 'US',
        'date': date,
        'close': last_close,
        'fusion_level': fusion.level.value,
        'fusion_score': fusion.score,
        'fusion_confidence': fusion.confidence,
        'target_position': fusion.position_pct,
        'risk': fusion.risk_level,
        'warnings': [str(w) for w in fusion.warnings] if fusion.warnings else [],
        'reasoning': fusion.reasoning,
        'fm_signal': sig,
        'fm_score': round(score, 2),
        'xmm_signal': xmm_action,
        'rsi_daily': round(float(rsi_d[-1]), 1),
        'rsi_weekly': round(float(w_rsi), 1),
        'fractal_type': ftype,
        'trend_up': bool(trend_up),
        'llm_factor_score': round(llm_factor_score, 1),
    }


# ─── 风控管理器 ─────────────────────────────────────────
class USRiskManager:
    STATE_FILE = str(STATE_DIR / 'risk_state.json')

    def __init__(self, total_assets, cash, positions):
        self.total_assets = total_assets
        self.cash = cash
        self.positions = {p['code']: p for p in positions}
        self.state = self._load_state()
        self.highest_prices = self.state.get('highest_prices', {})
        self.entry_prices = self.state.get('entry_prices', {})
        self.portfolio_peak = self.state.get('portfolio_peak', total_assets)
        self.stop_timestamps = self.state.get('stop_timestamps', {})
        self.latest_atr = self.state.get('latest_atr', {})
        for code, pos in self.positions.items():
            if code not in self.entry_prices:
                self.entry_prices[code] = pos['cost_price']
            if code not in self.highest_prices:
                self.highest_prices[code] = pos['current_price']

    def _load_state(self):
        if os.path.exists(self.STATE_FILE):
            try: return json.load(open(self.STATE_FILE, encoding='utf-8'))
            except: pass
        return {}

    def _save_state(self):
        self.state.update({
            'highest_prices': self.highest_prices,
            'entry_prices': self.entry_prices,
            'portfolio_peak': self.portfolio_peak,
            'stop_timestamps': self.stop_timestamps,
            'latest_atr': self.latest_atr,
            'last_update': datetime.now().isoformat(),
        })
        with open(self.STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False, default=str)

    def update_prices(self):
        for code, pos in self.positions.items():
            if pos['current_price'] > self.highest_prices.get(code, 0):
                self.highest_prices[code] = pos['current_price']
        cur_val = self.cash + sum(p['market_val'] for p in self.positions.values())
        if cur_val > self.portfolio_peak:
            self.portfolio_peak = cur_val
        self._save_state()

    def set_atr_pct(self, code, atr_pct):
        self.latest_atr[code] = atr_pct

    def get_effective_stop(self, code):
        atr_pct = self.latest_atr.get(code, 0.04)
        return ATR_LOW_STOP_PCT if atr_pct < ATR_LOW_VOL_THRESH else FIXED_STOP_PCT

    def is_in_cooldown(self, code):
        ts_str = self.stop_timestamps.get(code)
        if not ts_str: return False
        try:
            days = (datetime.now() - datetime.fromisoformat(ts_str)).total_seconds() / 86400
            return days < STOP_COOLDOWN_DAYS
        except: return False

    def record_stop(self, code):
        self.stop_timestamps[code] = datetime.now().isoformat()
        self._save_state()

    def check_position_limit(self, code, trade_value, is_buy):
        if not is_buy: return True, 'OK'
        exposure = sum(p['market_val'] for p in self.positions.values())
        if exposure + trade_value > self.total_assets * MAX_TOTAL_PCT:
            return False, f'Total would exceed {int(MAX_TOTAL_PCT*100)}%'
        stock_val = self.positions.get(code, {}).get('market_val', 0)
        if stock_val + trade_value > self.total_assets * MAX_POSITION_PCT:
            return False, f'Single {code} would exceed {int(MAX_POSITION_PCT*100)}%'
        return True, 'OK'

    def check_fixed_stop(self, code):
        pos = self.positions.get(code)
        if not pos or pos.get('qty', 0) <= 0: return False, 0
        pnl = pos['current_price'] / pos['cost_price'] - 1 if pos['cost_price'] > 0 else 0
        return pnl <= self.get_effective_stop(code), pnl

    def check_trailing_stop(self, code):
        pos = self.positions.get(code)
        if not pos or pos.get('qty', 0) <= 0: return False, 0
        high = self.highest_prices.get(code, pos['current_price'])
        dd = pos['current_price'] / high - 1 if high > 0 else 0
        return dd <= TRAILING_STOP_PCT, dd

    def check_take_profit(self, code):
        pos = self.positions.get(code)
        if not pos or pos.get('qty', 0) <= 0: return False, 0
        pnl = pos['current_price'] / pos['cost_price'] - 1 if pos['cost_price'] > 0 else 0
        return pnl >= 0.20, pnl  # US: take profit +20%

    def cleanup_closed(self, active_codes):
        for code in list(self.entry_prices.keys()):
            if code not in active_codes:
                self.entry_prices.pop(code, None)
                self.highest_prices.pop(code, None)
                self.stop_timestamps.pop(code, None)
                self.latest_atr.pop(code, None)
        self._save_state()


# ─── 主流程 ─────────────────────────────────────────────
def run_us_pipeline(dry_run=True):
    ts_start = datetime.now()
    today = ts_start.strftime('%Y-%m-%d')

    print('=' * 70)
    print(f'  US Stock Pipeline v2.0 (FusionEngine)')
    print(f'  Time: {ts_start.strftime("%Y-%m-%d %H:%M")} | Mode: {"DRY-RUN" if dry_run else "LIVE"}')
    print('=' * 70)

    # Step 1: 信号扫描
    print(f'\n--- STEP 1: Signal Scan ({len(US_UNIVERSE)} stocks) ---')
    signals = []
    for code in US_UNIVERSE:
        print(f'  Analyzing {code}...', end=' ', flush=True)
        result = fetch_and_analyze_us(code)
        if result:
            signals.append(result)
            print(f'→ {result["fusion_level"]} ({result["fusion_score"]:+.1f})')
        else:
            print('→ FAILED')

    signal_map = {s['code']: s for s in signals}
    buys = [s for s in signals if s['fusion_level'] in ('BUY', 'STRONG_BUY')]
    sells = [s for s in signals if s['fusion_level'] in ('SELL', 'STRONG_SELL')]
    print(f'\n  Total: {len(signals)} | BUY: {len(buys)} | SELL: {len(sells)}')

    # Step 2: 账户 + 持仓
    print(f'\n--- STEP 2: Account & Positions ---')
    ctx = ft.OpenSecTradeContext(host=FUTU_HOST, port=FUTU_PORT, filter_trdmarket=ft.Market.US)
    ret, acc = ctx.accinfo_query(trd_env=TRD_ENV)
    if ret != ft.RET_OK:
        print(f'  [ERROR] Account query failed: {acc}')
        ctx.close()
        return

    a = acc.iloc[0]
    total_assets = float(a.get('total_assets', 0))
    cash = float(a.get('cash', 0))
    print(f'  Total: ${total_assets:,.0f} | Cash: ${cash:,.0f}')

    ret, pdata = ctx.position_list_query(trd_env=TRD_ENV)
    positions = []
    if ret == ft.RET_OK and pdata is not None:
        for _, r in pdata.iterrows():
            qty = float(r['qty'])
            cost = float(r['cost_price'])
            val = float(r.get('market_val', 0))
            cur = val / max(qty, 1)
            positions.append({
                'code': r['code'], 'name': r.get('stock_name', ''),
                'qty': qty, 'can_sell_qty': float(r.get('can_sell_qty', qty)),
                'cost_price': cost, 'current_price': cur, 'market_val': val,
            })
            pnl = (cur / cost - 1) * 100 if cost > 0 else 0
            print(f'  {r["code"]:<12} qty={qty:.0f} cost={cost:.2f} cur={cur:.2f} pnl={pnl:+.1f}%')
    print(f'  Holdings: {len(positions)} stocks')

    rm = USRiskManager(total_assets, cash, positions)
    rm.update_prices()
    rm.cleanup_closed({p['code'] for p in positions})

    sold_codes = set()
    trades = []

    # Step 3: 信号反转清仓（新增 — 对齐港股）
    print(f'\n--- STEP 3: Signal Reversal Exit ---')
    for pos in positions:
        code = pos['code']
        sig = signal_map.get(code)
        if not sig:
            continue
        level = sig['fusion_level']
        pnl = (pos['current_price'] / pos['cost_price'] - 1) if pos['cost_price'] > 0 else 0

        if level in SIGNAL_REVERSAL_CONFIG['trigger_levels']:
            if pnl > SIGNAL_REVERSAL_CONFIG['exclude_if_pnl_above']:
                print(f'  ⏸  HOLD: {code} signal={level} but pnl={pnl:+.1%}, let profits run')
                continue

            qty = int(pos['can_sell_qty'])
            if qty > 0:
                if dry_run:
                    print(f'  [DRY] SIGNAL_EXIT {code} {qty} @ {pos["current_price"]:.2f} (pnl={pnl:+.1%}) signal→{level}')
                else:
                    ret2, data2 = ctx.place_order(
                        price=pos['current_price'], qty=qty, code=code,
                        trd_side=ft.TrdSide.SELL, order_type=ft.OrderType.NORMAL, trd_env=TRD_ENV)
                    oid = data2.iloc[0].get('order_id', '') if ret2 == ft.RET_OK and len(data2) > 0 else 'ERR'
                    print(f'  [SIGNAL_EXIT] {code} {qty} @ {pos["current_price"]:.2f} → {oid} (pnl={pnl:+.1%})')
                trades.append({'code': code, 'side': 'SIGNAL_EXIT', 'qty': qty, 'price': pos['current_price'], 'pnl': f'{pnl:+.1%}'})
                sold_codes.add(code)

    if not any(t['side'] == 'SIGNAL_EXIT' for t in trades):
        print('  → 无信号反转触发清仓')

    # Step 4: 止损检查
    print(f'\n--- STEP 4: Stop-Loss Check ---')
    for code, pos in rm.positions.items():
        if code in sold_codes or pos.get('qty', 0) <= 0:
            continue

        triggered, reason = False, ''
        is_fixed, fixed_pnl = rm.check_fixed_stop(code)
        if is_fixed:
            triggered, reason = True, f'FIXED STOP pnl={fixed_pnl:+.1%}'
        if not triggered:
            is_trail, trail_dd = rm.check_trailing_stop(code)
            if is_trail:
                triggered, reason = True, f'TRAIL STOP dd={trail_dd:+.1%}'

        if triggered:
            qty = int(pos['can_sell_qty'])
            if qty > 0:
                rm.record_stop(code)
                if dry_run:
                    print(f'  [DRY] STOP {code} {qty} @ {pos["current_price"]:.2f} ({reason})')
                else:
                    ctx.place_order(price=pos['current_price'], qty=qty, code=code,
                                    trd_side=ft.TrdSide.SELL, order_type=ft.OrderType.NORMAL, trd_env=TRD_ENV)
                    print(f'  [STOP] {code} {qty} @ {pos["current_price"]:.2f} ({reason})')
                trades.append({'code': code, 'side': 'STOP', 'qty': qty, 'price': pos['current_price'], 'reason': reason})
                sold_codes.add(code)
        else:
            is_tp, tp_pnl = rm.check_take_profit(code)
            if is_tp:
                qty = int(pos['can_sell_qty'])
                if qty > 0:
                    if dry_run:
                        print(f'  [DRY] TP {code} {qty} @ {pos["current_price"]:.2f} (pnl={tp_pnl:+.1%})')
                    else:
                        ctx.place_order(price=pos['current_price'], qty=qty, code=code,
                                        trd_side=ft.TrdSide.SELL, order_type=ft.OrderType.NORMAL, trd_env=TRD_ENV)
                        print(f'  [TP] {code} {qty} @ {pos["current_price"]:.2f} (pnl={tp_pnl:+.1%})')
                    trades.append({'code': code, 'side': 'TP', 'qty': qty, 'price': pos['current_price']})
                    sold_codes.add(code)

    # Step 5: 建仓（分批建仓 — 对齐港股）
    print(f'\n--- STEP 5: Buy Signals (Staged Entry) ---')
    buy_signals = sorted([s for s in signals if s['fusion_level'] in ('BUY', 'STRONG_BUY')],
                         key=lambda x: x['fusion_score'], reverse=True)

    held_codes = {p['code'] for p in positions} - sold_codes
    lot = 1  # 美股无整手限制

    for sig in buy_signals:
        code = sig['code']
        if code in held_codes:
            continue
        if rm.is_in_cooldown(code):
            print(f'  [SKIP] {code} - cooldown')
            continue

        price = sig['close']
        if price <= 0:
            continue

        if sig['fusion_level'] == 'STRONG_BUY':
            budget = total_assets * 0.20
        else:
            budget = total_assets * 0.10

        budget = min(budget, cash * 0.8)
        qty = max(int(budget / price), 1)
        trade_val = price * qty

        ok, reason = rm.check_position_limit(code, trade_val, True)
        if not ok:
            print(f'  [SKIP] {code} - {reason}')
            continue

        # 分批建仓：首批只建 1/3
        if STAGED_ENTRY_CONFIG['enabled']:
            first_qty = max(int(qty * STAGED_ENTRY_CONFIG['first_pct']), 1)
            staged_info = f' [分批 1/{STAGED_ENTRY_CONFIG["total_tranches"]} 总目标{qty}股]'
            qty = first_qty

        if dry_run:
            print(f'  [DRY] BUY {code} {qty} @ {price:.2f} (score={sig["fusion_score"]:+.1f}){staged_info}')
        else:
            ret2, data2 = ctx.place_order(
                price=price, qty=qty, code=code,
                trd_side=ft.TrdSide.BUY, order_type=ft.OrderType.NORMAL, trd_env=TRD_ENV)
            oid = data2.iloc[0].get('order_id', '') if ret2 == ft.RET_OK and len(data2) > 0 else 'ERR'
            print(f'  [BUY] {code} {qty} @ {price:.2f} → {oid} (score={sig["fusion_score"]:+.1f}){staged_info}')
            cash -= trade_val

        trades.append({'code': code, 'side': 'BUY', 'qty': qty, 'price': price,
                        'fusion_level': sig['fusion_level'], 'fusion_score': sig['fusion_score']})

    ctx.close()

    # 保存报告
    report = {
        'timestamp': datetime.now().isoformat(),
        'mode': 'DRY-RUN' if dry_run else 'LIVE',
        'pipeline_version': 'v2.0',
        'total_assets': total_assets, 'cash': cash,
        'signals': {
            'total': len(signals),
            'buys': len(buys),
            'sells': len(sells),
        },
        'trades': trades,
        'signal_details': [{k: v for k, v in s.items()} for s in signals],
    }
    ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = OUTPUT_DIR / f'us_report_{ts_str}.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    # 保存信号到 paper_trading/signals（统一信号源）
    sig_dir = PT / 'signals'
    os.makedirs(sig_dir, exist_ok=True)
    sig_path = sig_dir / f'{today}_us.json'
    with open(sig_path, 'w', encoding='utf-8') as f:
        json.dump({'date': today, 'market': 'US', 'signals': signals}, f, indent=2, ensure_ascii=False, default=str)

    print(f'\n{"="*70}')
    print(f'  Summary')
    print(f'{"="*70}')
    print(f'  Signals: {len(signals)} | BUY: {len(buys)} | SELL: {len(sells)}')
    print(f'  Trades: {len(trades)} | Report: {path}')
    print(f'  Signal file: {sig_path}')
    print(f'  Duration: {(datetime.now() - ts_start).total_seconds():.1f}s')
    print(f'{"="*70}')

    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='US Stock Pipeline v2.0')
    parser.add_argument('--live', action='store_true', help='Execute trades on Futu')
    args = parser.parse_args()
    run_us_pipeline(dry_run=not args.live)
