from concurrent.futures import ThreadPoolExecutor
import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from ..config import Config
from ..events import read_observations
from ..market import DAY_MS, DataError, PublicOKX, validate_bars, validate_quote
from ..portfolio import PaperLedger, empty_state, equity, execute_batch
from ..runner import run
from ..strategy import Strategy


def fixture(n=140):
    rng = np.random.default_rng(42)
    prices = 100 + np.cumsum(rng.normal(0, 1, n))
    origin = 1735689600000
    return [[str(origin + i * DAY_MS), str(v), str(v + 1), str(v - 1), str(v + .1), '1000', '100000', '100000', '1'] for i, v in enumerate(prices)]


def signal(symbol='BTC-USDT', action='BUY', fraction=.6, day=1):
    return {'symbol': symbol, 'action': action, 'raw_fraction': fraction,
            'event_id': f'{symbol}-{day}', 'signal_close_ms': day * DAY_MS,
            'signal_status': 'ELIGIBLE', 'data_status': 'VALID'}


def rules(symbol='BTC-USDT'):
    return {'instId': symbol, 'instType': 'SPOT', 'quoteCcy': 'USDT', 'state': 'live',
            'lotSz': '0.000001', 'minSz': '0.000001', 'tickSz': '0.01'}


class DataTests(unittest.TestCase):
    def setUp(self):
        self.rows = fixture()
        self.now = int(self.rows[-1][0]) + DAY_MS + 1000

    def test_confirm_and_utc_latest(self):
        current = list(self.rows[-1]); current[0] = str(self.now // DAY_MS * DAY_MS); current[8] = '0'
        bars = validate_bars([current] + self.rows[::-1], self.now)
        self.assertEqual(len(bars), 140)
        self.assertEqual(int(bars.iloc[-1]['ts']), int(self.rows[-1][0]))

    def test_missing_duplicate_stale_invalid_and_nonfinite(self):
        cases = []
        cases.append((self.rows[:80] + self.rows[81:], 'MISSING_DAILY_BAR'))
        cases.append((self.rows + [self.rows[-1]], 'DUPLICATE_TIMESTAMP'))
        cases.append((self.rows[:-1], 'STALE_DAILY_BARS'))
        bad = copy.deepcopy(self.rows); bad[0][2] = '1'
        cases.append((bad, 'INVALID_OHLCV'))
        bad = copy.deepcopy(self.rows); bad[0][4] = 'nan'
        cases.append((bad, 'NONFINITE_CANDLE'))
        for rows, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(DataError, error):
                validate_bars(rows, self.now)

    def test_future_confirmation_and_utc8_rejected(self):
        rows = copy.deepcopy(self.rows)
        rows[-1][0] = str(self.now // DAY_MS * DAY_MS)
        with self.assertRaisesRegex(DataError, 'FUTURE_CONFIRMED'):
            validate_bars(rows, self.now)
        rows = copy.deepcopy(self.rows)
        rows[0][0] = str(int(rows[0][0]) + 8 * 3600 * 1000)
        with self.assertRaisesRegex(DataError, 'UTC_BOUNDARY'):
            validate_bars(rows, self.now)

    def test_insufficient_confirmed(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row[8] = '0'
        with self.assertRaisesRegex(DataError, 'INSUFFICIENT_CONFIRMED'):
            validate_bars(rows, self.now)

    def test_private_route_cannot_be_called(self):
        calls = []
        client = PublicOKX(opener=lambda *a, **k: calls.append(a))
        with self.assertRaisesRegex(DataError, 'PUBLIC_ROUTE_NOT_ALLOWED'):
            client.get('/api/v5/trade/order')
        self.assertEqual(calls, [])

    def test_quote_time_and_spread(self):
        q = {'instId': 'BTC-USDT', 'ts': str(self.now), 'bidPx': '100', 'askPx': '101'}
        self.assertEqual(validate_quote('BTC-USDT', q, self.now, Config(), self.now - 1000)['bid'], 100)
        with self.assertRaisesRegex(DataError, 'STALE_QUOTE'):
            validate_quote('BTC-USDT', q, self.now + 100000, Config(), self.now - 1000)
        q['askPx'] = '99'
        with self.assertRaisesRegex(DataError, 'QUOTE_INVALID'):
            validate_quote('BTC-USDT', q, self.now, Config(), self.now - 1000)


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(Config(initial_cash=85), symbols=('BTC-USDT',))
        self.quotes = {'BTC-USDT': {'bid': 100., 'ask': 100., 'ts': DAY_MS}}
        self.rules = {'BTC-USDT': rules()}

    def test_sixty_percent_signal_capped_at_twenty(self):
        state, result = execute_batch(empty_state(self.config), [signal()], self.quotes, self.rules, self.config)
        self.assertEqual(result['orders'][0]['target_weight'], .2)
        self.assertLessEqual(state['positions']['BTC-USDT']['qty'] * 100 / equity(state, {'BTC-USDT': 100}), .2)
        self.assertGreater(state['fees_paid'], 0)

    def test_sell_reduces_and_cannot_open_short(self):
        state, _ = execute_batch(empty_state(self.config), [signal(action='SELL', fraction=1)], self.quotes, self.rules, self.config)
        self.assertFalse(state['positions'])
        state, _ = execute_batch(state, [signal()], self.quotes, self.rules, self.config)
        qty = state['positions']['BTC-USDT']['qty']
        sell = signal(action='SELL', fraction=.5, day=2)
        q = copy.deepcopy(self.quotes); q['BTC-USDT']['ts'] = 2 * DAY_MS
        state, result = execute_batch(state, [sell], q, self.rules, self.config)
        self.assertLessEqual(state['positions']['BTC-USDT']['qty'], qty * .5 + 1e-6)
        self.assertEqual(result['orders'][0]['side'], 'SELL')

    def test_portfolio_gross_cap_and_min_size(self):
        cfg = replace(Config(initial_cash=85), symbols=('BTC-USDT', 'ETH-USDT'), single_cap=.5, gross_cap=.6)
        sig = [signal(s) for s in cfg.symbols]
        quotes = {s: {'bid': 100., 'ask': 100., 'ts': DAY_MS} for s in cfg.symbols}
        ins = {s: rules(s) for s in cfg.symbols}
        state, result = execute_batch(empty_state(cfg), sig, quotes, ins, cfg)
        self.assertAlmostEqual(sum(x['target_weight'] for x in result['orders']), .6)
        nav = equity(state, {s: 100 for s in cfg.symbols})
        self.assertLessEqual(sum(p['qty'] * 100 for p in state['positions'].values()) / nav, .6)
        ins['BTC-USDT']['minSz'] = '100'
        _, result = execute_batch(empty_state(cfg), sig, quotes, ins, cfg)
        self.assertEqual(next(x for x in result['orders'] if x['symbol'] == 'BTC-USDT')['status'], 'REJECTED_MIN_SIZE')

    def test_idempotency_revision_and_atomic_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = PaperLedger(Path(folder) / 'paper.db', self.config)
            first, _ = ledger.apply([signal()], self.quotes, self.rules)
            second, result = ledger.apply([signal()], self.quotes, self.rules)
            self.assertEqual(first, second)
            self.assertEqual(result['status'], 'DUPLICATE_SUPPRESSED')
            with self.assertRaisesRegex(ValueError, 'SIGNAL_REVISION_CONFLICT'):
                ledger.apply([signal(fraction=.4)], self.quotes, self.rules)
            bad = signal(day=2)
            with self.assertRaisesRegex(ValueError, 'FILL_BEFORE_SIGNAL'):
                ledger.apply([bad], self.quotes, self.rules)
            q = copy.deepcopy(self.quotes); q['BTC-USDT']['ts'] = 2 * DAY_MS
            _, result = ledger.apply([bad], q, self.rules)
            self.assertEqual(result['status'], 'PAPER_APPLIED')

    def test_concurrent_duplicate_commits_once(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = PaperLedger(Path(folder) / 'paper.db', self.config)
            with ThreadPoolExecutor(max_workers=2) as pool:
                outputs = list(pool.map(lambda _: ledger.apply([signal()], self.quotes, self.rules)[1]['status'], range(2)))
            self.assertCountEqual(outputs, ['PAPER_APPLIED', 'DUPLICATE_SUPPRESSED'])

    def test_same_accounting_in_memory_and_sqlite(self):
        expected, result = execute_batch(empty_state(self.config), [signal()], self.quotes, self.rules, self.config)
        with tempfile.TemporaryDirectory() as folder:
            actual, saved = PaperLedger(Path(folder) / 'paper.db', self.config).apply([signal()], self.quotes, self.rules)
        self.assertEqual(expected, actual)
        self.assertEqual(result['orders'], saved['orders'])

    def test_hold_no_rebalance_and_configuration_pinned(self):
        state, _ = execute_batch(empty_state(self.config), [signal()], self.quotes, self.rules, self.config)
        q = copy.deepcopy(self.quotes); q['BTC-USDT']['ts'] = 2 * DAY_MS
        held, result = execute_batch(state, [signal(action='HOLD', fraction=0, day=2)], q, self.rules, self.config)
        self.assertEqual(held['positions'], state['positions'])
        self.assertEqual(result['orders'][0]['status'], 'NO_CHANGE')
        with self.assertRaisesRegex(ValueError, 'CONFIG_CHANGED'):
            execute_batch(state, [signal()], self.quotes, self.rules, replace(self.config, single_cap=.1))

    def test_failed_trim_cannot_finance_new_risk(self):
        cfg = replace(Config(initial_cash=85), symbols=('BTC-USDT', 'ETH-USDT'), single_cap=.4, gross_cap=.6)
        state = empty_state(cfg)
        state['cash'] = 40
        state['positions'] = {'BTC-USDT': {'qty': .6, 'cost': 60}}
        quotes = {s: {'bid': 100., 'ask': 100., 'ts': DAY_MS} for s in cfg.symbols}
        ins = {s: rules(s) for s in cfg.symbols}
        ins['BTC-USDT']['minSz'] = '1'
        signals = [signal('BTC-USDT', 'HOLD', 0), signal('ETH-USDT', 'BUY', .4)]
        actual, result = execute_batch(state, signals, quotes, ins, cfg)
        eth = next(x for x in result['orders'] if x['symbol'] == 'ETH-USDT')
        self.assertEqual(eth['status'], 'REJECTED_PROJECTED_RISK')
        self.assertEqual(actual['positions'].get('ETH-USDT', {}).get('qty', 0), 0)
        self.assertFalse(result['risk_resolved'])

    def test_nan_configuration_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Nonfinite'):
            replace(self.config, initial_cash=float('nan'))


class PipelineTests(unittest.TestCase):
    def test_prefix_consistency_and_td_ablation(self):
        rows = fixture()
        bars = validate_bars(rows, int(rows[-1][0]) + DAY_MS + 1000)
        strategy = Strategy(Config())
        frames = strategy.frames(bars)
        past = strategy.signal(bars, 'BTC-USDT', frames=frames, index=129)
        truncated = strategy.signal(bars.iloc[:130], 'BTC-USDT')
        self.assertEqual(past, truncated)
        full = strategy.signal(bars, 'BTC-USDT', frames=frames)
        no_td = strategy.signal(bars, 'BTC-USDT', 'trend_structure', frames)
        self.assertEqual((full['action'], full['raw_fraction']), (no_td['action'], no_td['raw_fraction']))

    def test_data_error_is_not_hold_and_no_forward_db_on_replay(self):
        snapshot = {'schema_version': 1, 'source': 'OKX_PUBLIC_REST', 'bar': '1Dutc',
                    'clock_ok': True, 'server_ms': 0, 'captured_at': '2026-09-03T00:00:00+00:00',
                    'symbols': {}}
        with tempfile.TemporaryDirectory() as folder:
            pointer, artifact = run(folder, snapshot=snapshot)
            self.assertEqual(artifact['counts']['HOLD'], 0)
            self.assertEqual(artifact['counts']['DATA_ERROR'], 5)
            self.assertEqual(artifact['status'], 'PARTIAL')
            self.assertFalse((Path(folder) / 'paper.sqlite3').exists())
            self.assertTrue(Path(pointer['report']).is_file())

    def test_llm_future_record_rejected(self):
        event = {'observed_at': '2026-09-03T01:00:00Z', 'published_at': '2026-09-03T00:00:00Z',
                 'source_url': 'https://example.com/event', 'model': 'fixture', 'symbol': 'BTC-USDT', 'summary': 'Synthetic event'}
        from datetime import datetime, timezone
        cutoff = int(datetime(2026, 9, 3, tzinfo=timezone.utc).timestamp() * 1000)
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'events.jsonl'; p.write_text(json.dumps(event), encoding='utf-8')
            result = read_observations(p, cutoff, ['BTC-USDT'])
        self.assertEqual(result['accepted'], [])
        self.assertEqual(result['position_effect'], 0)
        self.assertTrue(result['rejected'])


if __name__ == '__main__':
    unittest.main()
