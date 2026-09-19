"""One-off manual DOGE top-up on OKX demo, explicitly authorized by Roy on 2026-09-12.

Exception record: batch 20260912T040828-2d7099c599 planned DOGE target 20% but the
IOC only filled 7276.483071 / 151146.257089 (thin demo book at the 5bps-slippage
limit price). Roy authorized a one-off manual top-up to the same 20% target.

Rules honored:
- Reuses DemoBroker (demo-only, spot cash IOC, clOrdId nsc*, x-simulated-trading=1).
- Does NOT touch output/okx_demo_execution.sqlite3 batch journal; this trade lives
  outside batch batches and will appear to the next run only as a new baseline.
- Same target formula as demo_execution.plan_orders (20% cap, reserve buffer).
- Slippage ladder 0.3%/0.6%/1.0% above refreshed ask, IOC only; stops when gap
  filled or ladder exhausted. No credentials are printed or persisted.
"""
import json
import math
import secrets
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'E:/quant')

from research.northstar_crypto.config import Config
from research.northstar_crypto.demo_broker import DemoBroker
from research.northstar_crypto.demo_execution import rounded, validate_receipt, validate_account, exposure
from research.northstar_crypto.market import DataError

SYMBOL = 'DOGE-USDT'
BASE = 'DOGE'
STRATEGY_TARGET_WEIGHT = 0.20   # today's batch target for DOGE (raw 40% capped at single_cap)
SOL_RAW_WEIGHT = 0.20           # today's batch SOL BUY raw fraction, for gross-scaling fidelity
LADDER = [0.003, 0.006, 0.010]  # max tolerances above ask, in order
TERMINAL = {'filled', 'canceled', 'mmp_canceled'}
USDT_BUFFER = 10.0

config = Config()
broker = DemoBroker()
record = {'purpose': 'MANUAL_TOPUP_ROY_AUTHORIZED_2026_09_12',
          'ref_batch': '20260912T040828-2d7099c599', 'symbol': SYMBOL,
          'started_at': datetime.now(timezone.utc).isoformat()}

def floor_lot(qty, lot):
    from decimal import ROUND_FLOOR
    return float((Decimal(str(max(0.0, qty))) / Decimal(str(lot))).to_integral_value(rounding=ROUND_FLOOR) * Decimal(str(lot)))

def abort(reason, **kw):
    record.update(status='ABORTED', reason=reason, **kw)
    dump()
    print(json.dumps({'status': 'ABORTED', 'reason': reason, **{k: v for k, v in kw.items()}}, ensure_ascii=False))
    sys.exit(2)

def dump():
    out_dir = Path(__file__).parent / 'output' / 'manual_topup' / record['started_at'][:19].replace(':', '').replace('-', '')
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'record.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    record['record_dir'] = str(out_dir)

# --- 1. identity & account state -------------------------------------------------
ident = broker.identify()
record['account_id'] = ident['account_id']
account = broker.capture()
validate_account(account)
if account['pending_orders']:
    abort('PENDING_ORDERS_PRESENT', pending=account['pending_orders'])

nav = account['total_equity_usdt']
usdt_usd = account['usdt_usd_rate']
avail = account['available_usdt']
currencies = {r['currency']: r for r in account['currencies']}
doge_now = currencies[BASE]['cash_balance']
fee = broker.fee_rate(SYMBOL)

# --- 2. instrument rules & quote -------------------------------------------------
inst = broker.get('/api/v5/public/instruments', instType='SPOT', instId=SYMBOL)[0]
lot, tick, min_sz = inst['lotSz'], inst['tickSz'], inst['minSz']
ticker = broker.get('/api/v5/market/ticker', instId=SYMBOL)[0]
ask, bid = float(ticker['askPx']), float(ticker['bidPx'])
if not (math.isfinite(ask) and math.isfinite(bid) and 0 < bid <= ask):
    abort('INVALID_QUOTE', ask=ticker.get('askPx'), bid=ticker.get('bidPx'))
mid = (ask + bid) / 2
spread = (ask - bid) / mid

# --- 3. target per plan_orders formula -------------------------------------------
values = {s: currencies.get(s.split('-')[0], {}).get('equity_usd', 0) / usdt_usd for s in config.symbols}
managed_ccy = {s.split('-')[0] for s in config.symbols} | {'USDT'}
outside = sum(r['equity_usd'] / usdt_usd for r in account['currencies'] if r['currency'] not in managed_ccy)
current_doge_weight = values[SYMBOL] / nav
current_sol_weight = values['SOL-USDT'] / nav
# mirror plan_orders: BUY desired = max(current, min(raw, single_cap)); DOGE raw 40% -> 0.20
doge_desired = min(max(current_doge_weight, min(0.40, config.single_cap)), config.single_cap)
sol_desired = min(max(current_sol_weight, min(SOL_RAW_WEIGHT, config.single_cap)), config.single_cap)
available_gross = max(0.0, config.gross_cap - outside / nav)
total = sol_desired + doge_desired
if total > available_gross:
    doge_desired = doge_desired * available_gross / total
reserve = 2 * (fee + config.slippage_bps / 10000 + spread)
if reserve >= 0.10:
    abort('EXCESSIVE_EXECUTION_COST', reserve=reserve)
target_qty = floor_lot(doge_desired * nav * (1 - reserve) / (ask * (1 + config.slippage_bps / 10000)), lot)
gap = target_qty - doge_now
if gap < float(min_sz):
    record.update(status='NO_ACTION', doge_now=doge_now, target_qty=target_qty, gap=gap)
    dump()
    print(json.dumps({'status': 'NO_ACTION', 'gap': gap, 'target_qty': target_qty, 'doge_now': doge_now}, ensure_ascii=False))
    sys.exit(0)

# cap by available USDT
px_est = ask * (1 + LADDER[-1])
if gap * px_est > avail - USDT_BUFFER:
    gap = floor_lot((avail - USDT_BUFFER) / px_est, lot)
    if gap < float(min_sz):
        abort('INSUFFICIENT_USDT_FOR_MIN_SIZE', avail=avail)

record.update(status='EXECUTING', nav_usdt=nav, usdt_usd_rate=usdt_usd, available_usdt=avail,
              doge_before=doge_now, doge_weight_before=current_doge_weight,
              doge_target_weight=doge_desired, target_qty=target_qty, gap_qty=gap,
              ask=ask, bid=bid, spread=spread, fee_rate=fee, reserve=reserve,
              lotSz=lot, tickSz=tick, minSz=min_sz)
dump()

# --- 4. slippage ladder of IOC buys ----------------------------------------------
remaining = gap
fills = []
for tol in LADDER:
    if remaining < float(min_sz):
        break
    ticker = broker.get('/api/v5/market/ticker', instId=SYMBOL)[0]
    ask_t = float(ticker['askPx'])
    px = rounded(ask_t * (1 + tol), tick, up=False)
    sz = rounded(remaining, lot, up=False)
    if float(sz) < float(min_sz):
        break
    cl = 'nsc' + secrets.token_hex(8)
    body = {'instId': SYMBOL, 'clOrdId': cl, 'tdMode': 'cash', 'ordType': 'ioc', 'side': 'buy', 'sz': sz, 'px': px}
    entry = {'tolerance': tol, 'ask': ask_t, 'body': body}
    try:
        ack = broker.place_ioc(body)
        entry['ack'] = {'ordId': ack.get('ordId'), 'sCode': ack.get('sCode'), 'sMsg': ack.get('sMsg')}
    except DataError as exc:
        # never assume failure: query by clOrdId before deciding
        try:
            row = broker.order(SYMBOL, cl)
        except Exception:
            entry['error'] = str(exc)
            fills.append(entry)
            record.update(status='ABORTED', reason='PLACE_FAILED_AND_QUERY_FAILED', error=str(exc))
            dump()
            break
        row = None
    # settle to terminal state
    row = None
    for _ in range(12):
        row = broker.order(SYMBOL, cl)
        if row.get('state') in TERMINAL:
            break
        time.sleep(0.5)
    if row is None or row.get('state') not in TERMINAL:
        entry['error'] = 'ORDER_NOT_TERMINAL'
        fills.append(entry)
        record.update(status='ABORTED', reason='ORDER_NOT_TERMINAL', clOrdId=cl)
        dump()
        break
    validate_receipt(body, row)
    filled = float(row.get('accFillSz') or 0)
    entry['receipt'] = {k: row.get(k) for k in ('ordId', 'state', 'accFillSz', 'avgPx', 'fee', 'feeCcy', 'rebate', 'rebateCcy', 'cancelSource')}
    fills.append(entry)
    remaining -= filled
    record['fills'] = fills
    record['remaining_after_ladder_step'] = remaining
    dump()

record['fills'] = fills
total_filled = sum(float(f['receipt']['accFillSz']) for f in fills if f.get('receipt'))
total_cost = sum(float(f['receipt']['accFillSz']) * float(f['receipt']['avgPx']) for f in fills if f.get('receipt'))
total_fee = sum(abs(float(f['receipt'].get('fee') or 0)) for f in fills if f.get('receipt'))

# --- 5. post-trade account & risk verification ------------------------------------
time.sleep(1.0)
after = broker.capture()
c_after = {r['currency']: r for r in after['currencies']}
nav_after = after['total_equity_usdt']
doge_after = c_after[BASE]['cash_balance']
usdt_after = after['available_usdt']
doge_value_after = c_after[BASE]['equity_usd'] / after['usdt_usd_rate']
doge_weight_after = doge_value_after / nav_after
values_after = {s: c_after.get(s.split('-')[0], {}).get('equity_usd', 0) / after['usdt_usd_rate'] for s in config.symbols}
gross_after = (sum(values_after.values()) + outside) / nav_after
risk_ok = doge_weight_after <= config.single_cap + 1e-6 and gross_after <= config.gross_cap + 1e-8

record.update(status='DONE', total_filled=total_filled, total_cost_usdt=total_cost,
              total_fee_doge=total_fee, remaining_gap=remaining,
              doge_after=doge_after, usdt_after=usdt_after, nav_after=nav_after,
              doge_weight_after=doge_weight_after, gross_weight_after=gross_after,
              risk_ok=risk_ok,
              finished_at=datetime.now(timezone.utc).isoformat())
dump()

print(json.dumps({
    'status': 'DONE', 'account_id': ident['account_id'],
    'gap_qty': gap, 'filled_qty': total_filled, 'remaining_gap': remaining,
    'total_cost_usdt': round(total_cost, 4), 'total_fee_doge': total_fee,
    'orders': [{'ordId': f['receipt']['ordId'], 'px_limit': f['body']['px'], 'sz': f['body']['sz'],
                'accFillSz': f['receipt']['accFillSz'], 'avgPx': f['receipt']['avgPx'],
                'state': f['receipt']['state'], 'fee': f['receipt']['fee']} for f in fills if f.get('receipt')],
    'doge_before': doge_now, 'doge_after': doge_after,
    'doge_weight_before': round(current_doge_weight, 6), 'doge_weight_after': round(doge_weight_after, 6),
    'gross_weight_after': round(gross_after, 6), 'risk_ok': risk_ok,
    'nav_before_usdt': nav, 'nav_after_usdt': nav_after,
}, ensure_ascii=False, indent=2))
