"""One accounting engine for replay and forward paper runs. No broker access."""
import copy
from contextlib import closing
from decimal import Decimal, ROUND_FLOOR
import math
import sqlite3

from .io_utils import canonical, digest


def empty_state(config):
    if config.initial_cash is None:
        raise ValueError('ACCOUNT_FUNDING_REQUIRED')
    return {'cash': config.initial_cash, 'initial_cash': config.initial_cash,
            'positions': {}, 'fees_paid': 0.0, 'realized_pnl': 0.0,
            'config_hash': config.fingerprint(), 'last_signal_close_ms': 0}


def equity(state, marks):
    return state['cash'] + sum(p['qty'] * marks[s] for s, p in state['positions'].items())


def target_weights(signals, state, marks, config):
    """BUY is desired exposure; SELL reduces existing long; HOLD has no alpha delta.

    Caps can independently trim drifted exposure. Gross excess is scaled uniformly.
    """
    nav = equity(state, marks)
    if nav <= 0 or not math.isfinite(nav):
        raise ValueError('INVALID_EQUITY')
    targets = {}
    for s in signals:
        symbol = s['symbol']
        current = state['positions'].get(symbol, {}).get('qty', 0) * marks[symbol] / nav
        fraction = float(s['raw_fraction'])
        if not math.isfinite(fraction) or not 0 <= fraction <= 1:
            raise ValueError('INVALID_SIGNAL_FRACTION')
        if s['action'] == 'BUY':
            desired = max(current, min(fraction, config.single_cap))
        elif s['action'] == 'SELL':
            desired = current * (1 - fraction)
        elif s['action'] == 'HOLD':
            desired = current
        else:
            raise ValueError('INVALID_ACTION')
        targets[symbol] = min(desired, config.single_cap)
    total = sum(targets.values())
    if total > config.gross_cap:
        targets = {s: w * config.gross_cap / total for s, w in targets.items()}
    return nav, targets


def floor_lot(qty, lot):
    return float((Decimal(str(max(0, qty))) / Decimal(str(lot))).to_integral_value(rounding=ROUND_FLOOR) * Decimal(str(lot)))


def execute_batch(state, signals, quotes, instruments, config):
    """Deterministic local simulated fills, also used by historical replay."""
    state = copy.deepcopy(state)
    if state['config_hash'] != config.fingerprint():
        raise ValueError('CONFIG_CHANGED_USE_NEW_LEDGER')
    symbols = {s['symbol'] for s in signals}
    if len(symbols) != len(signals) or symbols != set(config.symbols):
        raise ValueError('INCOMPLETE_OR_DUPLICATE_UNIVERSE')
    if not set(state['positions']).issubset(symbols):
        raise ValueError('UNMANAGED_POSITION')
    times = {s['signal_close_ms'] for s in signals}
    if len(times) != 1 or next(iter(times)) < state['last_signal_close_ms']:
        raise ValueError('OUT_OF_ORDER_BATCH')
    for s in signals:
        if s['signal_status'] != 'ELIGIBLE' or s['data_status'] != 'VALID':
            raise ValueError('INELIGIBLE_SIGNAL')
    marks = {}
    max_spread = 0.0
    for symbol in symbols:
        q = quotes[symbol]
        if not all(math.isfinite(q[k]) for k in ('bid', 'ask')) or not 0 < q['bid'] <= q['ask']:
            raise ValueError('INVALID_QUOTE')
        if q['ts'] < next(iter(times)):
            raise ValueError('FILL_BEFORE_SIGNAL')
        marks[symbol] = (q['bid'] + q['ask']) / 2
        max_spread = max(max_spread, (q['ask'] - q['bid']) / marks[symbol])
    nav, targets = target_weights(signals, state, marks, config)
    fee_rate, slip = config.fee_bps / 10000, config.slippage_bps / 10000
    reserve = 2 * (fee_rate + slip + max_spread)
    if reserve >= 0.10:
        raise ValueError('EXCESSIVE_EXECUTION_COST')
    by_symbol = {s['symbol']: s for s in signals}
    orders = []
    for symbol in sorted(symbols):
        current_qty = state['positions'].get(symbol, {}).get('qty', 0.0)
        current_weight = current_qty * marks[symbol] / nav
        desired = targets[symbol]
        # HOLD and a weaker BUY do not rebalance an otherwise compliant position.
        unchanged = abs(desired - current_weight) < 1e-12
        target_qty = current_qty if unchanged else floor_lot(
            desired * nav * (1 - reserve) / (quotes[symbol]['ask'] * (1 + slip)), instruments[symbol]['lotSz'])
        delta = target_qty - current_qty
        orders.append({'symbol': symbol, 'event_id': by_symbol[symbol]['event_id'],
                       'action': by_symbol[symbol]['action'], 'raw_fraction': by_symbol[symbol]['raw_fraction'],
                       'current_qty': current_qty, 'target_weight': desired,
                       'target_qty': target_qty, 'delta_qty': delta,
                       'risk_trim': desired + 1e-12 < current_weight and by_symbol[symbol]['action'] != 'SELL',
                       'status': 'NO_CHANGE'})
    for order in sorted(orders, key=lambda x: x['delta_qty']):  # Sells before buys.
        symbol, delta = order['symbol'], order['delta_qty']
        if abs(delta) < 1e-12:
            continue
        rules, q = instruments[symbol], quotes[symbol]
        qty = floor_lot(abs(delta), rules['lotSz'])
        if qty < float(rules['minSz']) or qty <= 0:
            order['status'] = 'REJECTED_MIN_SIZE'
            continue
        side = 'BUY' if delta > 0 else 'SELL'
        px = q['ask'] * (1 + slip) if side == 'BUY' else q['bid'] * (1 - slip)
        notional, fee = qty * px, qty * px * fee_rate
        position = state['positions'].setdefault(symbol, {'qty': 0.0, 'cost': 0.0})
        if side == 'BUY':
            if notional + fee > state['cash'] + 1e-10:
                order['status'] = 'REJECTED_CASH'
                continue
            projected_nav = equity(state, marks) - fee - qty * (px - marks[symbol])
            projected_values = {s: p['qty'] * marks[s] for s, p in state['positions'].items()}
            projected_values[symbol] = projected_values.get(symbol, 0) + qty * marks[symbol]
            if projected_nav <= 0 or sum(projected_values.values()) > config.gross_cap * projected_nav + 1e-8 or any(v > config.single_cap * projected_nav + 1e-8 for v in projected_values.values()):
                order['status'] = 'REJECTED_PROJECTED_RISK'
                continue
            state['cash'] -= notional + fee
            position['qty'] += qty
            position['cost'] += notional + fee
        else:
            if qty > position['qty'] + 1e-10:
                raise ValueError('SHORT_POSITION_FORBIDDEN')
            allocated_cost = position['cost'] * min(1.0, qty / position['qty'])
            position['qty'] = max(0.0, position['qty'] - qty)
            position['cost'] = max(0.0, position['cost'] - allocated_cost)
            state['cash'] += notional - fee
            state['realized_pnl'] += notional - fee - allocated_cost
        state['fees_paid'] += fee
        order.update(status='SIMULATED_FILL', side=side, filled_qty=qty, fill_price=px,
                     fee=fee, fill_ts=q['ts'], venue_order_id=None)
    if state['cash'] < -1e-8 or any(p['qty'] < 0 for p in state['positions'].values()):
        raise ValueError('ACCOUNTING_INVARIANT_FAILED')
    state['last_signal_close_ms'] = next(iter(times))
    final_nav = equity(state, marks)
    final_weights = {s: p['qty'] * marks[s] / final_nav for s, p in state['positions'].items()}
    risk_resolved = sum(final_weights.values()) <= config.gross_cap + 1e-8 and all(v <= config.single_cap + 1e-8 for v in final_weights.values())
    return state, {'equity_before': nav, 'equity_after': final_nav,
                   'actual_weights': final_weights, 'risk_resolved': risk_resolved,
                   'orders': orders, 'mode': 'LOCAL_PAPER_ONLY', 'actual_orders': 0}


class PaperLedger:
    """SQLite transaction makes signal consumption and balance changes atomic."""
    def __init__(self, path, config):
        self.path, self.config = str(path), config
        with closing(sqlite3.connect(self.path, timeout=15)) as con, con:
            con.executescript('CREATE TABLE IF NOT EXISTS book (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);'
                              'CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, hash TEXT NOT NULL, result TEXT NOT NULL);')
            con.execute('INSERT OR IGNORE INTO book VALUES (1,?)', (canonical(empty_state(config)),))

    def apply(self, signals, quotes, instruments):
        import json
        with closing(sqlite3.connect(self.path, timeout=15)) as con, con:
            con.execute('BEGIN IMMEDIATE')
            state = json.loads(con.execute('SELECT payload FROM book WHERE id=1').fetchone()[0])
            if state['config_hash'] != self.config.fingerprint():
                raise ValueError('CONFIG_CHANGED_USE_NEW_LEDGER')
            existing = []
            for signal in signals:
                row = con.execute('SELECT hash,result FROM events WHERE id=?', (signal['event_id'],)).fetchone()
                if row and row[0] != digest(signal):
                    raise ValueError('SIGNAL_REVISION_CONFLICT')
                existing.append(row)
            if any(existing):
                if not all(existing):
                    raise ValueError('PARTIALLY_CONSUMED_BATCH')
                return state, {'status': 'DUPLICATE_SUPPRESSED', 'actual_orders': 0,
                               'orders': [], 'risk_resolved': json.loads(existing[0][1]).get('risk_resolved', True),
                               'previous_result': json.loads(existing[0][1])}
            state, result = execute_batch(state, signals, quotes, instruments, self.config)
            result['status'] = 'PAPER_APPLIED'
            con.execute('UPDATE book SET payload=? WHERE id=1', (canonical(state),))
            for signal in signals:
                con.execute('INSERT INTO events VALUES (?,?,?)', (signal['event_id'], digest(signal), canonical(result)))
            return state, result
