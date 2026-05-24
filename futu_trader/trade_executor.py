# -*- coding: utf-8 -*-
"""
Futu Simulated Trading Executor v1.0
Reads signals from signal_engine -> Applies risk rules -> Places orders via Futu API

Author: AiGobot
Date: 2026-04-24
"""
import sys, io, os, json, warnings, argparse
from datetime import datetime
import numpy as np
import pandas as pd

_IS_MAIN = __name__ == '__main__'
if _IS_MAIN:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

# === Config ===
FUTU_HOST = '127.0.0.1'
FUTU_PORT = 11111
SIGNAL_DIR = r'E:\quant\futu_trader\output'
TRADE_LOG_DIR = r'E:\quant\futu_trader\output'

# Simulated account
TRD_ENV = 'SIMULATE'
CASH_ACC_ID = 0       # 0 = use default first SIMULATE CASH account
MARGIN_ACC_ID = 0

# Risk limits
MAX_POSITION_PCT = 0.20       # single stock max 20% of total assets
MAX_TOTAL_PCT = 0.80          # total position max 80% of total assets

# Triple stop-loss (aligned with full_backtest_v2)
FIXED_STOP_PCT = -0.08        # Fixed stop -8% (default)
TRAILING_STOP_PCT = -0.08     # Trailing stop from highest price -8%
PORTFOLIO_DD_PCT = -0.25      # Portfolio drawdown warning threshold
TAKE_PROFIT_PCT = 0.15        # take profit +15%

# ATR-adaptive stop: for low-volatility stocks, relax stop to avoid washout
ATR_LOW_VOL_THRESH = 0.03     # ATR < 3% of price = low vol, use wider stop
ATR_LOW_STOP_PCT = -0.15      # Relax stop to -15% for low-vol stocks (wider than daily noise)

# Cooldown period after stop-loss: prevent immediate re-entry
STOP_COOLDOWN_DAYS = 10       # Days to wait before re-entering same stock after a stop

MIN_TRADE_QTY = 100           # HK stocks minimum lot size (round to 100)

# Signal thresholds
STRONG_BUY = 'STRONG_BUY'
BUY = 'BUY'
STRONG_SELL = 'STRONG_SELL'
SELL = 'SELL'
HOLD = 'HOLD'

os.makedirs(TRADE_LOG_DIR, exist_ok=True)
STATE_DIR = r'E:\quant\futu_trader\state'
os.makedirs(STATE_DIR, exist_ok=True)


# === Risk Manager (Triple Stop-Loss + State Persistence) ===
class RiskManager:
    STATE_FILE = os.path.join(STATE_DIR, 'risk_state.json')

    def __init__(self, total_assets, cash, positions):
        self.total_assets = total_assets
        self.cash = cash
        self.positions = {p['code']: p for p in positions}
        self.state = self._load_state()
        self.highest_prices = self.state.get('highest_prices', {})
        self.entry_prices = self.state.get('entry_prices', {})
        self.portfolio_peak = self.state.get('portfolio_peak', total_assets)
        self.stop_timestamps = self.state.get('stop_timestamps', {})  # {code: datetime} cooldown tracking
        self.latest_atr = self.state.get('latest_atr', {})            # {code: atr_pct}
        self.warnings = []

        # Initialize entries for new positions (track from state or use cost_price)
        for code, pos in self.positions.items():
            if code not in self.entry_prices:
                self.entry_prices[code] = pos['cost_price']
            if code not in self.highest_prices:
                self.highest_prices[code] = pos['current_price']

    def _load_state(self):
        if os.path.exists(self.STATE_FILE):
            try:
                with open(self.STATE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_state(self):
        self.state['highest_prices'] = self.highest_prices
        self.state['entry_prices'] = self.entry_prices
        self.state['portfolio_peak'] = self.portfolio_peak
        self.state['stop_timestamps'] = self.stop_timestamps
        self.state['latest_atr'] = self.latest_atr
        self.state['last_update'] = datetime.now().isoformat()
        with open(self.STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False, default=str)

    def set_atr_pct(self, code, atr_pct):
        """Store ATR as % of price for adaptive stop decisions"""
        self.latest_atr[code] = atr_pct

    def get_effective_stop(self, code):
        """Return effective stop % based on ATR. Low vol -> relaxed stop"""
        atr_pct = self.latest_atr.get(code, 0.05)  # default 5% if unknown
        if atr_pct < ATR_LOW_VOL_THRESH:
            return ATR_LOW_STOP_PCT
        return FIXED_STOP_PCT

    def is_in_cooldown(self, code):
        """Check if stock is in stop-loss cooldown period"""
        ts_str = self.stop_timestamps.get(code)
        if not ts_str:
            return False
        try:
            stop_time = datetime.fromisoformat(ts_str)
            days_since = (datetime.now() - stop_time).total_seconds() / 86400
            return days_since < STOP_COOLDOWN_DAYS
        except Exception:
            return False

    def record_stop(self, code):
        """Record a stop-loss event to start cooldown"""
        self.stop_timestamps[code] = datetime.now().isoformat()
        self._save_state()

    def update_prices(self):
        """Update highest prices and portfolio peak for current positions"""
        current_total = self.cash
        for code, pos in self.positions.items():
            current_price = pos['current_price']
            current_total += pos['market_val']
            # Update trailing high
            if current_price > self.highest_prices.get(code, 0):
                self.highest_prices[code] = current_price
        # Update portfolio peak
        if current_total > self.portfolio_peak:
            self.portfolio_peak = current_total
        self._save_state()

    def check_position_limit(self, code, trade_value, is_buy):
        if not is_buy:
            return True, 'SELL always allowed'
        current_exposure = sum(p['market_val'] for p in self.positions.values())
        if current_exposure + trade_value > self.total_assets * MAX_TOTAL_PCT:
            return False, 'Total position would exceed {}%'.format(int(MAX_TOTAL_PCT * 100))
        current_stock_val = self.positions.get(code, {}).get('market_val', 0)
        if current_stock_val + trade_value > self.total_assets * MAX_POSITION_PCT:
            return False, 'Single stock {} would exceed {}%'.format(code, int(MAX_POSITION_PCT * 100))
        return True, 'OK'

    def check_fixed_stop(self, code):
        """Adaptive fixed stop: uses wider stop for low-vol stocks to avoid washout"""
        pos = self.positions.get(code)
        if not pos or pos.get('qty', 0) <= 0:
            return False, 0
        cost = pos['cost_price']
        current = pos['current_price']
        if cost <= 0 or current <= 0:
            return False, 0
        pnl_pct = (current / cost - 1)
        stop_pct = self.get_effective_stop(code)
        return pnl_pct <= stop_pct, pnl_pct

    def check_trailing_stop(self, code):
        """Trailing stop: drawdown from highest price since entry >= 8%"""
        pos = self.positions.get(code)
        if not pos or pos.get('qty', 0) <= 0:
            return False
        current = pos['current_price']
        highest = self.highest_prices.get(code, current)
        if highest <= 0 or current <= 0:
            return False
        dd = (current / highest - 1)
        return dd <= TRAILING_STOP_PCT, dd

    def check_portfolio_drawdown(self):
        """Portfolio drawdown warning from peak >= 25%"""
        current_val = self.cash + sum(p['market_val'] for p in self.positions.values())
        if self.portfolio_peak <= 0:
            return False
        dd = (current_val / self.portfolio_peak - 1)
        if dd <= PORTFOLIO_DD_PCT:
            return True, dd
        return False, dd

    def check_take_profit(self, code):
        pos = self.positions.get(code)
        if not pos or pos.get('qty', 0) <= 0:
            return False
        cost = pos['cost_price']
        current = pos['current_price']
        if cost <= 0 or current <= 0:
            return False
        pnl_pct = (current / cost - 1)
        return pnl_pct >= TAKE_PROFIT_PCT, pnl_pct

    def calc_buy_qty(self, code, price, signal_strength):
        if signal_strength >= 0.015:
            pct = 0.20
        elif signal_strength >= 0.005:
            pct = 0.10
        else:
            pct = 0.05
        budget = self.total_assets * pct
        qty = int(budget / price)
        qty = (qty // MIN_TRADE_QTY) * MIN_TRADE_QTY
        return max(qty, 0)

    def cleanup_closed_positions(self, active_codes):
        """Remove state for positions that are no longer held"""
        for code in list(self.entry_prices.keys()):
            if code not in active_codes:
                self.entry_prices.pop(code, None)
                self.highest_prices.pop(code, None)
                self.stop_timestamps.pop(code, None)
                self.latest_atr.pop(code, None)
        self._save_state()


# === Trade Executor ===
class TradeExecutor:
    def __init__(self, dry_run=True):
        self.dry_run = dry_run
        self.trade_log = []
        self.ctx = None

    def connect(self):
        from futu import OpenSecTradeContext
        self.ctx = OpenSecTradeContext(
            filter_trdmarket='HK',
            host=FUTU_HOST,
            port=FUTU_PORT,
        )
        print('[CONNECT] Trade context ready')

    def disconnect(self):
        if self.ctx:
            self.ctx.close()
            print('[DISCONNECT] Trade context closed')

    def get_positions(self):
        from futu import RET_OK, TrdEnv
        ret, data = self.ctx.position_list_query(trd_env=TRD_ENV)
        if ret != RET_OK:
            print('[ERROR] position_list_query: {}'.format(data))
            return []
        positions = []
        for _, row in data.iterrows():
            positions.append({
                'code': row['code'],
                'name': row.get('stock_name', ''),
                'qty': float(row['qty']),
                'can_sell_qty': float(row.get('can_sell_qty', row['qty'])),
                'cost_price': float(row['cost_price']),
                'market_val': float(row.get('market_val', 0)),
                'current_price': float(row.get('market_val', 0)) / max(float(row['qty']), 1),
            })
        return positions

    def get_account_info(self):
        from futu import RET_OK
        ret, data = self.ctx.accinfo_query(trd_env=TRD_ENV)
        if ret != RET_OK:
            print('[ERROR] accinfo_query: {}'.format(data))
            return {'total_assets': 0, 'cash': 0, 'power': 0}
        row = data.iloc[0]
        return {
            'total_assets': float(row.get('total_assets', 0)),
            'cash': float(row.get('cash', 0)),
            'power': float(row.get('power', 0)),
            'market_val': float(row.get('market_val', 0)),
        }

    def place_order(self, code, price, qty, trd_side):
        from futu import TrdSide, OrderType, TrdEnv, RET_OK

        if self.dry_run:
            print('[DRY-RUN] {} {} {} @ {:.2f}'.format(trd_side, qty, code, price))
            self.trade_log.append({
                'time': datetime.now().isoformat(),
                'code': code, 'side': str(trd_side),
                'price': price, 'qty': qty,
                'dry_run': True,
            })
            return True, 'DRY-RUN OK'

        ret, data = self.ctx.place_order(
            price=price,
            qty=qty,
            code=code,
            trd_side=trd_side,
            order_type=OrderType.NORMAL,
            trd_env=TrdEnv.SIMULATE,
        )
        if ret == RET_OK:
            order_id = data.iloc[0].get('order_id', 'N/A') if len(data) > 0 else 'N/A'
            print('[ORDER] {} {} {} @ {:.2f} -> order_id={}'.format(
                trd_side, qty, code, price, order_id))
            self.trade_log.append({
                'time': datetime.now().isoformat(),
                'code': code, 'side': str(trd_side),
                'price': price, 'qty': qty,
                'order_id': str(order_id),
                'dry_run': False,
            })
            return True, str(order_id)
        else:
            print('[ERROR] place_order: {}'.format(data))
            return False, str(data)

    def get_latest_price(self, code):
        from futu import OpenQuoteContext, RET_OK, SubType
        quote_ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
        ret, data = quote_ctx.get_market_snapshot([code])
        quote_ctx.close()
        if ret == RET_OK and len(data) > 0:
            return float(data.iloc[0].get('last_price', 0))
        return 0.0


def _fetch_atr_async(rm, positions):
    """Background ATR fetch for adaptive stop decisions (best-effort, silent on fail)"""
    try:
        from futu import OpenQuoteContext, RET_OK
        ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
        for p in positions:
            try:
                ret, df, _ = ctx.request_history_kline(
                    code=p['code'], start='2026-04-01', end='2026-04-30', ktype='K_DAY', autype='qfq')
                if ret == RET_OK and len(df) >= 14:
                    high = df['high'].values.astype(float)
                    low = df['low'].values.astype(float)
                    close = df['close'].values.astype(float)
                    tr = [max(high[i]-low[i], abs(high[i]-(close[i-1] if i>0 else close[i])), abs(low[i]-(close[i-1] if i>0 else close[i])))
                          for i in range(len(close))]
                    atr14 = sum(tr[-14:]) / 14
                    rm.set_atr_pct(p['code'], atr14 / close[-1])
            except Exception:
                pass
        ctx.close()
    except Exception:
        pass


# === Main Trading Logic ===
def run_trader(signal_file=None, dry_run=True):
    from futu import TrdSide

    executor = TradeExecutor(dry_run=dry_run)
    executor.connect()

    try:
        # 1. Get account info
        acc_info = executor.get_account_info()
        total_assets = acc_info['total_assets']
        print('\n[ACCOUNT] Total: {:.0f} HKD | Cash: {:.0f} | Power: {:.0f}'.format(
            total_assets, acc_info['cash'], acc_info['power']))

        # 2. Get current positions
        positions = executor.get_positions()
        print('[POSITIONS] {} stocks held'.format(len(positions)))
        for p in positions:
            pnl = (p['current_price'] / p['cost_price'] - 1) * 100 if p['cost_price'] > 0 else 0
            print('  {} {} qty={:.0f} cost={:.2f} cur={:.2f} pnl={:+.1f}%'.format(
                p['code'], p['name'], p['qty'], p['cost_price'], p['current_price'], pnl))

        # 3. Load signals
        if signal_file:
            with open(signal_file, 'r', encoding='utf-8') as f:
                signal_data = json.load(f)
            signals = signal_data.get('results', [])
        else:
            latest = sorted([f for f in os.listdir(SIGNAL_DIR) if f.startswith('signal_') and f.endswith('.json')])
            if not latest:
                print('[WARN] No signal files found in {}'.format(SIGNAL_DIR))
                return
            with open(os.path.join(SIGNAL_DIR, latest[-1]), 'r', encoding='utf-8') as f:
                signal_data = json.load(f)
            signals = signal_data.get('results', [])
            print('[SIGNALS] Loaded from {}'.format(latest[-1]))

        # Build signal map
        signal_map = {s['code']: s for s in signals if 'signal' in s}

        # 4. Initialize risk manager
        rm = RiskManager(total_assets, acc_info['cash'], positions)
        rm.update_prices()  # Update highs + portfolio peak before any decisions

        # Fetch live ATR for adaptive stop
        _fetch_atr_async(rm, positions)

        # Clean state for positions that have been closed
        active_codes = {p['code'] for p in positions}
        rm.cleanup_closed_positions(active_codes)

        sold_codes = set()  # Track stocks already sold to avoid duplicate orders

        # 5. Process SELL signals (sell first to free cash)
        for code, sig in signal_map.items():
            if sig['signal'] in (STRONG_SELL, SELL):
                pos = rm.positions.get(code)
                if pos and pos['can_sell_qty'] > 0:
                    qty = int(pos['can_sell_qty'])
                    qty = (qty // MIN_TRADE_QTY) * MIN_TRADE_QTY
                    if qty > 0:
                        price = pos['current_price']
                        reason = 'signal={}'.format(sig['signal'])
                        print('\n[SELL] {} {} @ {:.2f} ({})'.format(code, qty, price, reason))
                        executor.place_order(code, price, qty, TrdSide.SELL)
                        sold_codes.add(code)

        # 6. Check triple stop-loss for existing positions
        #     Priority: fixed stop > trailing stop > portfolio DD (warning only)
        for code, pos in rm.positions.items():
            if code in sold_codes:
                continue
            if pos.get('qty', 0) <= 0 or pos.get('can_sell_qty', 0) <= 0:
                continue

            triggered, reason = False, ''
            qty = int(pos['can_sell_qty'])
            qty = (qty // MIN_TRADE_QTY) * MIN_TRADE_QTY
            if qty <= 0:
                continue

            # 1) Fixed stop -8%
            is_fixed, fixed_pnl = rm.check_fixed_stop(code)
            if is_fixed:
                triggered, reason = True, 'FIXED STOP pnl={:+.1%}'.format(fixed_pnl)
            
            # 2) Trailing stop -8% from high
            if not triggered:
                is_trail, trail_dd = rm.check_trailing_stop(code)
                if is_trail:
                    high = rm.highest_prices.get(code, 0)
                    triggered, reason = True, 'TRAIL STOP dd={:+.1%} from high={:.2f}'.format(trail_dd, high)

            if triggered:
                print('\n[STOP-LOSS] {} {} @ {:.2f} ({})'.format(code, qty, pos['current_price'], reason))
                rm.record_stop(code)  # start cooldown tracking
                executor.place_order(code, pos['current_price'], qty, TrdSide.SELL)
                sold_codes.add(code)
            else:
                # 3) Take profit +15%
                is_tp, tp_pnl = rm.check_take_profit(code)
                if is_tp:
                    print('\n[TAKE-PROFIT] {} {} @ {:.2f} (pnl={:+.1%})'.format(code, qty, pos['current_price'], tp_pnl))
                    executor.place_order(code, pos['current_price'], qty, TrdSide.SELL)
                    sold_codes.add(code)

        # 7. Process BUY signals
        for code, sig in signal_map.items():
            if sig['signal'] in (STRONG_BUY, BUY):
                price = sig.get('price', 0)
                if price <= 0:
                    price = executor.get_latest_price(code)
                if price <= 0:
                    print('\n[SKIP] {} - no price available'.format(code))
                    continue
                qty = rm.calc_buy_qty(code, price, sig.get('pred', 0))
                if qty <= 0:
                    print('\n[SKIP] {} - qty=0 (price too high or no budget)'.format(code))
                    continue
                # Cooldown check: prevent re-entry after stop-loss
                if rm.is_in_cooldown(code):
                    print('\n[SKIP] {} - in cooldown (stopped within {} days)'.format(code, STOP_COOLDOWN_DAYS))
                    continue
                trade_value = price * qty
                ok, reason = rm.check_position_limit(code, trade_value, is_buy=True)
                if ok:
                    print('\n[BUY] {} {} @ {:.2f} (signal={}, pred={:+.4f})'.format(
                        code, qty, price, sig['signal'], sig.get('pred', 0)))
                    executor.place_order(code, price, qty, TrdSide.BUY)
                else:
                    print('\n[SKIP] {} - risk check failed: {}'.format(code, reason))

    finally:
        executor.disconnect()

    # Save trade log
    if executor.trade_log:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_path = os.path.join(TRADE_LOG_DIR, 'trade_log_{}.json'.format(ts))
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'dry_run': dry_run,
                'trades': executor.trade_log,
            }, f, indent=2, ensure_ascii=False)
        print('\n[LOG] Saved: {}'.format(log_path))

    return executor.trade_log


# === CLI ===
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Futu Trade Executor')
    parser.add_argument('--signal-file', type=str, default=None, help='Path to signal JSON file')
    parser.add_argument('--live', action='store_true', help='Actually place orders (default: dry-run)')
    args = parser.parse_args()

    print('=' * 70)
    print('Futu Trade Executor v1.0')
    print('Time: {}'.format(datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    print('Mode: {}'.format('LIVE' if args.live else 'DRY-RUN'))
    print('=' * 70)

    trades = run_trader(signal_file=args.signal_file, dry_run=not args.live)

    print('\n' + '=' * 70)
    print('TRADE SUMMARY')
    print('=' * 70)
    if trades:
        for t in trades:
            print('  {} {} {} @ {:.2f} {}'.format(
                t['side'], t['qty'], t['code'], t['price'],
                '(DRY-RUN)' if t['dry_run'] else ''))
    else:
        print('  No trades executed')
    print('=' * 70)
