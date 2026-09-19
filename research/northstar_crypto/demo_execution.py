"""Durable demo exchange execution. The exchange, not a paper book, owns balances."""
from contextlib import contextmanager, closing
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import json
import math
import os
from pathlib import Path
import sqlite3
import time

from .config import Config
from .io_utils import canonical, digest
from .market import DataError

JOURNAL = Path(__file__).parent / 'output' / 'okx_demo_execution.sqlite3'
TERMINAL = {'filled', 'canceled', 'mmp_canceled'}


def rounded(value, step, up=False):
    value, step = Decimal(str(value)), Decimal(str(step))
    return format((value / step).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * step, 'f')


def balances(account):
    return {r['currency']: r['cash_balance'] for r in account['currencies']}


def validate_account(account, owned=()):
    if account.get('mode') != 'demo':
        raise DataError('DEMO_ACCOUNT_REQUIRED')
    if any(float(p.get('pos', 0)) != 0 for p in account['positions']):
        raise DataError('DERIVATIVE_POSITION_BLOCKS_SPOT_EXECUTION')
    if any(r['liability'] > 0 for r in account['currencies']):
        raise DataError('ACCOUNT_LIABILITY_BLOCKS_EXECUTION')
    if any(p.get('clOrdId') not in owned for p in account['pending_orders']):
        raise DataError('UNMANAGED_PENDING_ORDER')


def exposure(account, quotes, config):
    rows = {r['currency']: r for r in account['currencies']}
    nav = account['total_equity_usdt']
    managed = {s: rows.get(s.split('-')[0], {}).get('equity_usd', 0) /
               account['usdt_usd_rate'] for s in config.symbols}
    managed_ccy = {s.split('-')[0] for s in config.symbols} | {'USDT'}
    outside = sum(r['equity_usd'] / account['usdt_usd_rate'] for r in account['currencies']
                  if r['currency'] not in managed_ccy)
    return nav, managed, outside


def plan_orders(signals, account, quotes, instruments, config, fee):
    validate_account(account)
    if config.initial_cash is not None:
        raise DataError('DEMO_CAPITAL_MUST_BE_DYNAMIC')
    if len(signals) != len(config.symbols) or {s['symbol'] for s in signals} != set(config.symbols):
        raise DataError('INCOMPLETE_OR_DUPLICATE_UNIVERSE')
    if len({s['signal_close_ms'] for s in signals}) != 1:
        raise DataError('INCONSISTENT_SIGNAL_CLOSE')
    nav, values, outside = exposure(account, quotes, config)
    cash = balances(account)
    targets = {}
    for signal in signals:
        if signal['data_status'] != 'VALID' or signal['signal_status'] != 'ELIGIBLE':
            raise DataError('INELIGIBLE_SIGNAL')
        s, f, action = signal['symbol'], float(signal['raw_fraction']), signal['action']
        if not math.isfinite(f) or not 0 <= f <= 1:
            raise DataError('INVALID_SIGNAL_FRACTION')
        current = values[s] / nav
        if action == 'BUY':
            desired = max(current, min(f, config.single_cap))
        elif action == 'SELL':
            desired = current * (1 - f)
        elif action == 'HOLD':
            desired = current
        else:
            raise DataError('INVALID_ACTION')
        targets[s] = min(desired, config.single_cap)
    available_gross = max(0, config.gross_cap - outside / nav)
    total = sum(targets.values())
    if total > available_gross:
        targets = {s: w * available_gross / total for s, w in targets.items()}
    spread = max((q['ask'] - q['bid']) / ((q['ask'] + q['bid']) / 2) for q in quotes.values())
    reserve = 2 * (fee + config.slippage_bps / 10000 + spread)
    if reserve >= .1:
        raise DataError('EXCESSIVE_EXECUTION_COST')
    plans = []
    for signal in signals:
        s = signal['symbol']
        current_qty = cash.get(s.split('-')[0], 0)
        current = values[s] / nav
        target_qty = current_qty if abs(targets[s] - current) < 1e-12 else float(rounded(
            targets[s] * nav * (1 - reserve) / (quotes[s]['ask'] * (1 + config.slippage_bps / 10000)),
            instruments[s]['lotSz']))
        delta = target_qty - current_qty
        plan = {'symbol':s, 'action':signal['action'], 'current_qty':current_qty,
                'target_qty':target_qty, 'target_weight':targets[s], 'delta_qty':delta,
                'status':'NO_CHANGE', 'event_id':signal['event_id']}
        qty = rounded(abs(delta), instruments[s]['lotSz'])
        if float(qty) > 0 and float(qty) >= float(instruments[s]['minSz']):
            side = 'buy' if delta > 0 else 'sell'
            px = rounded(quotes[s]['ask'] * (1 + config.slippage_bps / 10000) if side == 'buy'
                         else quotes[s]['bid'] * (1 - config.slippage_bps / 10000),
                         instruments[s]['tickSz'], up=side == 'sell')
            plan.update(status='PLANNED', body={'instId':s, 'side':side, 'sz':qty, 'px':px,
                                                'tdMode':'cash', 'ordType':'ioc'})
        elif abs(delta) > 1e-12:
            plan['status'] = 'BELOW_MIN_SIZE'
        plans.append(plan)
    return sorted(plans, key=lambda p:p['delta_qty'])


@contextmanager
def execution_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + '.lock', 'a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise DataError('DEMO_EXECUTOR_ALREADY_RUNNING') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def validate_receipt(body, row):
    for key in ('instId', 'clOrdId', 'side', 'tdMode', 'ordType'):
        if row.get(key) != body[key]:
            raise DataError('ORDER_RECEIPT_MISMATCH:' + key)
    if not row.get('ordId') or Decimal(row['sz']) != Decimal(body['sz']) or Decimal(row['px']) != Decimal(body['px']):
        raise DataError('ORDER_RECEIPT_SIZE_PRICE_MISMATCH')
    fill = Decimal(row.get('accFillSz') or '0')
    avg = Decimal(row.get('avgPx') or '0')
    fee = Decimal(row.get('fee') or '0')
    rebate = Decimal(row.get('rebate') or '0')
    if not all(x.is_finite() for x in (fill, avg, fee, rebate)) or not 0 <= fill <= Decimal(body['sz']):
        raise DataError('INVALID_FILL_RECEIPT')
    if fill > 0 and (avg <= 0 or (body['side'] == 'buy' and avg > Decimal(body['px'])) or
                     (body['side'] == 'sell' and avg < Decimal(body['px']))):
        raise DataError('FILL_OUTSIDE_LIMIT')
    if fee and row.get('feeCcy') not in {body['instId'].split('-')[0], 'USDT'}:
        raise DataError('UNSUPPORTED_FEE_CURRENCY')
    if rebate and row.get('rebateCcy') not in {body['instId'].split('-')[0], 'USDT'}:
        raise DataError('UNSUPPORTED_REBATE_CURRENCY')
    return row


def reconcile(before, after, orders):
    expected = balances(before)
    for order in orders:
        r = order.get('receipt')
        if not r:
            continue
        qty, px = float(r.get('accFillSz') or 0), float(r.get('avgPx') or 0)
        sign = 1 if r['side'] == 'buy' else -1
        base = r['instId'].split('-')[0]
        expected[base] = expected.get(base, 0) + sign * qty
        expected['USDT'] = expected.get('USDT', 0) - sign * qty * px
        if r.get('feeCcy'):
            expected[r['feeCcy']] = expected.get(r['feeCcy'], 0) + float(r.get('fee') or 0)
        if r.get('rebateCcy'):
            expected[r['rebateCcy']] = expected.get(r['rebateCcy'], 0) + float(r.get('rebate') or 0)
    actual = balances(after)
    diffs = {ccy: actual.get(ccy, 0) - expected.get(ccy, 0) for ccy in set(actual) | set(expected)}
    mismatches = {c:v for c,v in diffs.items() if abs(v) > (1e-5 if c == 'USDT' else 1e-8)}
    return {'status':'PASS' if not mismatches else 'MISMATCH', 'balance_differences':diffs, 'mismatches':mismatches}


class DemoExecutor:
    def __init__(self, broker, config=None, journal=JOURNAL):
        self.broker, self.config, self.journal = broker, config or Config(), Path(journal)

    def _save(self, con, batch):
        con.execute('INSERT OR REPLACE INTO batches VALUES (?,?,?,?)',
                    (batch['key'], self.broker.account_id, batch['status'], canonical(batch)))
        con.commit()

    def _risk(self, batch, account, quotes):
        nav, values, outside = exposure(account, quotes, self.config)
        batch['actual_weights'] = {s:v/nav for s,v in values.items()}
        batch['gross_weight'] = (sum(values.values()) + outside) / nav
        batch['risk_resolved'] = batch['gross_weight'] <= self.config.gross_cap + 1e-8 and all(
            w <= self.config.single_cap + 1e-8 for w in batch['actual_weights'].values())

    def _settle(self, con, batch, order):
        body = order['body']
        try:
            row = None
            for attempt in range(3):
                row = validate_receipt(body, self.broker.order(body['instId'], body['clOrdId']))
                ack_id = order.get('acknowledgment',{}).get('ordId')
                if ack_id and ack_id != row['ordId']:
                    raise DataError('ACK_ORDER_ID_MISMATCH')
                if not order.get('accepted'):
                    order['accepted'] = True
                    batch['new_orders'] += 1
                order['receipt'] = row
                if row['state'] in TERMINAL:
                    order['status'] = row['state'].upper()
                    self._save(con, batch)
                    return
                time.sleep(.2)
            self.broker.cancel(body['instId'], body['clOrdId'])
            row = validate_receipt(body, self.broker.order(body['instId'], body['clOrdId']))
            order['receipt'] = row
            if row['state'] not in TERMINAL:
                raise DataError('ORDER_NOT_TERMINAL')
            order['status'] = row['state'].upper()
        except (DataError, ValueError, KeyError, OSError) as exc:
            order.update(status='UNKNOWN', error=str(exc)[:200])
        self._save(con, batch)

    def _check_dispatch(self, body, cutoff, fee, quotes):
        account = self.broker.capture()
        validate_account(account)
        symbol = body['instId']
        quote = self.broker.quote(symbol, self.config, cutoff)
        quotes[symbol] = quote
        qty, px = float(body['sz']), float(body['px'])
        rows = {r['currency']:r for r in account['currencies']}
        if body['side'] == 'sell':
            if qty > rows.get(symbol.split('-')[0], {}).get('available', 0) + 1e-12:
                raise DataError('INSUFFICIENT_AVAILABLE_BASE')
        else:
            if qty * px * (1 + fee) > account['available_usdt']:
                raise DataError('INSUFFICIENT_AVAILABLE_USDT')
            nav, values, outside = exposure(account, quotes, self.config)
            projected_nav = nav - qty * px * (fee + self.config.slippage_bps / 10000)
            values[symbol] += qty * max(px, quote['ask'])
            if (sum(values.values()) + outside > projected_nav * self.config.gross_cap or
                any(v > projected_nav * self.config.single_cap for v in values.values())):
                raise DataError('PROJECTED_EXPOSURE_LIMIT')
        # Never chase an adverse move outside the original IOC limit.
        if (body['side'] == 'buy' and quote['ask'] > px) or (body['side'] == 'sell' and quote['bid'] < px):
            raise DataError('QUOTE_MOVED_OUTSIDE_LIMIT')

    def _process(self, con, key, signature, plans, before, quotes, cutoff, fee, purpose):
        existing = con.execute('SELECT payload FROM batches WHERE key=?', (key,)).fetchone()
        if existing:
            batch = json.loads(existing[0])
            if batch['signature'] != signature:
                raise DataError('SIGNAL_REVISION_CONFLICT')
            if batch['status'] in {'SUBMITTING','UNKNOWN'}:
                for order in batch['orders']:
                    if order['status'] in {'SUBMITTING','UNKNOWN','ACKNOWLEDGED'}:
                        self._settle(con, batch, order)
                after = self.broker.capture()
                batch['account_after'] = after
                batch['reconciliation'] = reconcile(batch['account_before'], after, batch['orders'])
                resolved = all(o['status'] in {'FILLED','CANCELED','MMP_CANCELED','REJECTED'} for o in batch['orders'])
                batch['status'] = 'RECOVERED' if resolved and batch['reconciliation']['status'] == 'PASS' else 'UNKNOWN'
                self._save(con, batch)
            current = self.broker.capture()
            validate_account(current)
            self._risk(batch, current, quotes)
            return {**batch, 'current_account':current, 'duplicate_suppressed':True, 'new_orders':0}
        unresolved = con.execute("SELECT key FROM batches WHERE account=? AND status NOT IN ('APPLIED','RECOVERED')",
                                 (self.broker.account_id,)).fetchall()
        if unresolved:
            raise DataError('UNRESOLVED_PRIOR_BATCH:' + unresolved[0][0])
        orders = []
        for p in plans:
            if 'body' in p:
                body = dict(p['body'], clOrdId='nsc' + digest([self.broker.account_id,key,p['symbol']])[:29])
                self.broker._validate_request('POST','/api/v5/trade/order',body)
                orders.append({'body':body,'status':'PLANNED'})
        batch = {'key':key, 'signature':signature, 'purpose':purpose, 'mode':'OKX_DEMO',
                 'status':'SUBMITTING', 'orders':orders, 'plans':plans, 'account_before':before,
                 'new_orders':0, 'real_orders':0, 'fee_rate_bound':fee}
        self._save(con, batch)
        try:
            for order in orders:
                self._check_dispatch(order['body'], cutoff, fee, quotes)
                order['status'] = 'SUBMITTING'
                self._save(con, batch)  # Durable intent BEFORE any POST, including timeout/crash.
                try:
                    ack = self.broker.place_ioc(order['body'])
                    order['acknowledgment'] = ack
                    order['status'] = 'ACKNOWLEDGED' if ack.get('sCode') == '0' else 'REJECTED'
                    if order['status'] == 'ACKNOWLEDGED':
                        order['accepted'] = True
                        batch['new_orders'] += 1
                except (DataError, ValueError, OSError) as exc:
                    order.update(status='UNKNOWN', error=str(exc)[:200])
                self._save(con, batch)
                if order['status'] != 'REJECTED':
                    self._settle(con, batch, order)
                if order['status'] == 'UNKNOWN':
                    raise DataError('UNKNOWN_ORDER_OUTCOME_NO_RESUBMISSION')
            after = self.broker.capture()
            validate_account(after)
            batch['account_after'] = after
            batch['reconciliation'] = reconcile(before, after, orders)
            self._risk(batch, after, quotes)
            batch['status'] = 'APPLIED' if batch['reconciliation']['status'] == 'PASS' else 'BALANCE_MISMATCH'
        except (DataError, ValueError, KeyError, OSError) as exc:
            batch.update(status='UNKNOWN' if any(o['status'] == 'UNKNOWN' for o in orders) else 'BLOCKED',
                         error=str(exc)[:200])
            if batch['status'] == 'BLOCKED':
                # Close unsubmitted work without retrying it on the next daily run.
                for order in orders:
                    if order['status'] == 'PLANNED':
                        order['status'] = 'ABORTED_BEFORE_SUBMISSION'
                try:
                    after = self.broker.capture()
                    validate_account(after)
                    batch['account_after'] = after
                    batch['reconciliation'] = reconcile(before, after, orders)
                    self._risk(batch, after, quotes)
                    if batch['reconciliation']['status'] == 'PASS':
                        batch['status'] = 'APPLIED'
                except (DataError, ValueError, OSError):
                    pass
        self._save(con, batch)
        return batch

    @contextmanager
    def session(self):
        with execution_lock(self.journal), closing(sqlite3.connect(self.journal, timeout=15)) as con:
            con.execute('PRAGMA synchronous=FULL')
            con.execute('CREATE TABLE IF NOT EXISTS batches (key TEXT PRIMARY KEY, account TEXT, status TEXT, payload TEXT)')
            con.commit()
            yield con

    def apply(self, signals):
        cutoff = min(s['signal_close_ms'] for s in signals)
        with self.session() as con:
            instruments, quotes = self.broker.market_context(self.config, cutoff)
            account = self.broker.capture()
            fee = max(self.broker.fee_rate(s) for s in self.config.symbols)
            key = digest([self.broker.account_id, 'NORTHSTAR_DAILY', cutoff])
            signature = digest({'signals':signals,'config':self.config.payload()})
            for old_key, payload in con.execute("SELECT key,payload FROM batches WHERE account=? AND status IN ('SUBMITTING','UNKNOWN') AND key!=?",
                                                (self.broker.account_id,key)).fetchall():
                old = json.loads(payload)
                self._process(con,old_key,old['signature'],[],account,quotes,cutoff,fee,old['purpose'])
            account = self.broker.capture()
            # Recovery runs before planning so our own unresolved pending orders remain queryable.
            if con.execute('SELECT 1 FROM batches WHERE key=?', (key,)).fetchone():
                plans = []
            else:
                plans = plan_orders(signals, account, quotes, instruments, self.config, fee)
            return self._process(con, key, signature, plans, account, quotes, cutoff, fee, 'NORTHSTAR_STRATEGY')

    def connectivity_order(self, symbol, side, qty, test_id):
        """Explicit engineering probe, maximum 10 USDT per leg; never an alpha signal."""
        with self.session() as con:
            instruments, quotes = self.broker.market_context(self.config, 0)
            before = self.broker.capture()
            validate_account(before)
            fee = self.broker.fee_rate(symbol)
            rules, q = instruments[symbol], quotes[symbol]
            sz = rounded(qty, rules['lotSz'])
            px = rounded(q['ask'] * 1.0005 if side == 'buy' else q['bid'] * .9995,
                         rules['tickSz'], up=side == 'sell')
            if side not in {'buy','sell'} or float(sz) < float(rules['minSz']) or float(sz)*float(px) > 10:
                raise DataError('CONNECTIVITY_TEST_BUDGET_OR_SIZE')
            body = {'instId':symbol,'side':side,'sz':sz,'px':px,'tdMode':'cash','ordType':'ioc'}
            key = digest([self.broker.account_id,'CONNECTIVITY_TEST',test_id,symbol,side])
            return self._process(con,key,digest([test_id,symbol,side,sz]),[{'symbol':symbol,'body':body}],
                                 before,quotes,0,fee,'ENGINEERING_CONNECTIVITY_TEST')
