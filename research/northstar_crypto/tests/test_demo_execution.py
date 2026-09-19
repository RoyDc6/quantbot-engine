import copy
import tempfile
from pathlib import Path
import unittest

from ..config import Config
from ..demo_broker import DemoBroker
from ..demo_execution import DemoExecutor, execution_lock, plan_orders, reconcile, validate_receipt
from ..market import DataError


def account(cash=1000, eth=0):
    rows = [{'currency':'USDT','cash_balance':cash,'equity':cash,'equity_usd':cash,
             'available':cash,'frozen':0,'liability':0},
            {'currency':'ETH','cash_balance':eth,'equity':eth,'equity_usd':eth*100,
             'available':eth,'frozen':0,'liability':0}]
    return {'mode':'demo','account_id':'unit','currencies':rows,'total_equity_usdt':cash+eth*100,
            'usdt_usd_rate':1,'available_usdt':cash,'positions':[],'pending_orders':[]}


class FakeBroker:
    account_id = 'unit'
    def __init__(self):
        self.a = account()
        self.posts = 0
        self.orders = {}
        self.fraction = 1
        self.timeout = False
        self.query_fail = False
    def capture(self):
        return copy.deepcopy(self.a)
    def fee_rate(self, symbol):
        return .001
    def market_context(self, config, cutoff):
        return ({s:{'lotSz':'.001','minSz':'.001','tickSz':'.01'} for s in config.symbols},
                {s:{'bid':100.,'ask':100.,'ts':cutoff} for s in config.symbols})
    def quote(self, symbol, config, cutoff):
        return {'bid':100.,'ask':100.,'ts':cutoff}
    def _validate_request(self, *args):
        pass
    def place_ioc(self, body):
        self.posts += 1
        qty = float(body['sz']) * self.fraction
        fee = -qty*.001 if body['side']=='buy' else -qty*100*.001
        c = self.a['currencies']
        if body['side']=='buy':
            self.a = account(c[0]['cash_balance'] - qty*100, c[1]['cash_balance'] + qty + fee)
        else:
            self.a = account(c[0]['cash_balance'] + qty*100 + fee, c[1]['cash_balance']-qty)
        row = dict(body, ordId=str(self.posts), accFillSz=str(qty), avgPx='100' if qty else '',
                   fee=str(fee),feeCcy='ETH' if body['side']=='buy' else 'USDT',
                   state='filled' if self.fraction==1 else 'canceled')
        self.orders[body['clOrdId']] = row
        if self.timeout:
            raise DataError('TIMEOUT_AFTER_ACCEPTANCE')
        return {'sCode':'0','ordId':str(self.posts),'clOrdId':body['clOrdId']}
    def order(self, symbol, client):
        if self.query_fail:
            raise DataError('QUERY_TEMPORARILY_UNAVAILABLE')
        return copy.deepcopy(self.orders[client])


def signals():
    return [{'symbol':s,'signal_close_ms':1000,'data_status':'VALID','signal_status':'ELIGIBLE',
             'action':'BUY' if s=='ETH-USDT' else 'HOLD', 'raw_fraction':.2 if s=='ETH-USDT' else 0,
             'event_id':s} for s in Config().symbols]


class DemoExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'execution.sqlite3'
        self.b = FakeBroker()
        self.e = DemoExecutor(self.b,journal=self.path)

    def test_exchange_fills_balances_and_duplicate(self):
        first = self.e.apply(signals())
        self.assertEqual(first['status'],'APPLIED')
        self.assertEqual(first['reconciliation']['status'],'PASS')
        self.assertTrue(first['risk_resolved'])
        second = self.e.apply(signals())
        self.assertEqual(self.b.posts,1)
        self.assertTrue(second['duplicate_suppressed'])
        self.assertEqual(second['new_orders'],0)

    def test_partial_canceled_receipt_uses_actual_fill_and_fee(self):
        self.b.fraction = .4
        result = self.e.apply(signals())
        order = result['orders'][0]
        self.assertEqual(order['status'],'CANCELED')
        self.assertAlmostEqual(float(order['receipt']['accFillSz']),float(order['body']['sz'])*.4)
        self.assertEqual(result['reconciliation']['status'],'PASS')

    def test_timeout_after_fill_queries_without_second_post(self):
        self.b.timeout = True
        result = self.e.apply(signals())
        self.assertEqual(result['status'],'APPLIED')
        self.assertEqual(result['orders'][0]['status'],'FILLED')
        self.assertEqual(result['new_orders'],1)
        self.e.apply(signals())
        self.assertEqual(self.b.posts,1)

    def test_unknown_restart_recovers_without_resubmission(self):
        self.b.timeout = self.b.query_fail = True
        result = self.e.apply(signals())
        self.assertEqual(result['status'],'UNKNOWN')
        self.b.query_fail = False
        recovered = DemoExecutor(self.b,journal=self.path).apply(signals())
        self.assertEqual(recovered['status'],'RECOVERED')
        self.assertEqual(recovered['reconciliation']['status'],'PASS')
        self.assertTrue(recovered['risk_resolved'])
        self.assertEqual(self.b.posts,1)

    def test_signal_revision_does_not_place_again(self):
        self.e.apply(signals())
        revised = signals(); revised[1]['raw_fraction'] = .1
        with self.assertRaisesRegex(DataError,'REVISION'):
            self.e.apply(revised)
        self.assertEqual(self.b.posts,1)

    def test_unmanaged_order_blocks_before_post(self):
        self.b.a['pending_orders'] = [{'clOrdId':'manual'}]
        with self.assertRaisesRegex(DataError,'UNMANAGED_PENDING'):
            self.e.apply(signals())
        self.assertEqual(self.b.posts,0)

    def test_all_equity_is_capital_and_caps_remain(self):
        self.b.a = account(10000)
        instruments, quotes = self.b.market_context(Config(),1000)
        plans = plan_orders(signals(),self.b.a,quotes,instruments,Config(),.001)
        eth = next(p for p in plans if p['symbol']=='ETH-USDT')
        self.assertGreater(float(eth['body']['sz'])*100,1900)
        self.assertLess(float(eth['body']['sz'])*100,2000)

    def test_external_balance_change_is_not_strategy_pnl(self):
        result = reconcile(account(),account(999),[])
        self.assertEqual(result['status'],'MISMATCH')

    def test_receipt_identity_or_limit_violation_rejected(self):
        r = self.e.apply(signals())['orders'][0]
        wrong = dict(r['receipt'],avgPx='200')
        with self.assertRaisesRegex(DataError,'OUTSIDE_LIMIT'):
            validate_receipt(r['body'],wrong)
        with self.assertRaisesRegex(DataError,'MISMATCH'):
            validate_receipt(r['body'],dict(r['receipt'],clOrdId='another'))

    def test_demo_write_boundary_blocks_real_margin_and_other_assets(self):
        b = object.__new__(DemoBroker)
        b._credentials = {'OKX_FLAG':'1'}
        b.symbols = frozenset(Config().symbols)
        body = {'instId':'ETH-USDT','clOrdId':'nsctest','tdMode':'cash','ordType':'ioc',
                'side':'buy','sz':'.001','px':'100'}
        b._validate_request('POST','/api/v5/trade/order',body)
        for bad in (dict(body,tdMode='cross'),dict(body,instId='ETH-USDT-SWAP'),dict(body,sz='NaN')):
            with self.assertRaises(DataError):
                b._validate_request('POST','/api/v5/trade/order',bad)
        b._credentials['OKX_FLAG'] = '0'
        with self.assertRaisesRegex(DataError,'DEMO_CREDENTIALS'):
            b._validate_request('POST','/api/v5/trade/order',body)

    def test_process_lock_prevents_second_executor(self):
        with execution_lock(self.path):
            with self.assertRaisesRegex(DataError,'ALREADY_RUNNING'):
                with execution_lock(self.path):
                    pass

    def test_insufficient_funds_never_posts(self):
        original = self.b.capture
        calls = [0]
        def capture():
            calls[0] += 1
            row = original()
            if calls[0] >= 3:
                row['available_usdt'] = 0
            return row
        self.b.capture = capture
        result = self.e.apply(signals())
        self.assertEqual(self.b.posts,0)
        self.assertIn('INSUFFICIENT',result['error'])


if __name__ == '__main__':
    unittest.main()
