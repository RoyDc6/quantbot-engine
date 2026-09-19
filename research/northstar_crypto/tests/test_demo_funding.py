import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ..config import Config
from ..demo_account import DemoAccount, normalize_account
from ..funding import resolve_funding
from ..market import DataError
from ..portfolio import empty_state
from ..runner import run


def balance():
    return {'totalEq':'999', 'uTime':'1788400000000', 'details':[
        {'ccy':'USDT','eq':'900','cashBal':'900','eqUsd':'899.1','availBal':'850','frozenBal':'50'},
        {'ccy':'BTC','eq':'0.001','cashBal':'0.001','eqUsd':'99.9','availBal':'0.001','frozenBal':'0'}]}


class Client:
    def __init__(self, account): self.account = account
    def capture(self): return copy.deepcopy(self.account)


class DemoFundingTests(unittest.TestCase):
    def test_full_equity_not_available_cash_or_usd(self):
        a = normalize_account(balance(), [], [], 'testaccount')
        self.assertAlmostEqual(a['total_equity_usdt'], 1000)
        self.assertEqual(a['available_usdt'], 850)
        self.assertEqual(a['total_equity_usd'], 999)
        self.assertEqual(a['capital_allocation_fraction'], 1)

    def test_bad_or_unreconciled_account_never_invents_cash(self):
        for mutate in (lambda b:b.update(totalEq='NaN'),
                       lambda b:b.update(totalEq='5000'),
                       lambda b:b['details'][0].update(eq='0')):
            b = balance(); mutate(b)
            with self.assertRaises(DataError): normalize_account(b, [], [], 'test')
        self.assertIsNone(Config().initial_cash)
        with self.assertRaisesRegex(ValueError,'ACCOUNT_FUNDING_REQUIRED'): empty_state(Config())

    def test_live_credential_flag_is_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'.env';p.write_text('OKX_FLAG=0\nOKX_API_KEY=fake\nOKX_SECRET_KEY=fake\nOKX_PASSPHRASE=fake')
            with self.assertRaisesRegex(DataError,'DEMO_CREDENTIALS_REQUIRED'):
                DemoAccount(p, opener=lambda *a,**k:self.fail('network called'))
            p.write_text(p.read_text().replace('OKX_FLAG=0','OKX_FLAG=1'))
            c = DemoAccount(p, opener=lambda *a,**k:self.fail('network called'))
            with self.assertRaisesRegex(DataError,'DEMO_ROUTE_NOT_ALLOWED'): c.get('/api/v5/asset/transfer')
            with self.assertRaisesRegex(DataError,'DEMO_READ_ONLY'): c._request('POST','/api/v5/trade/order')

    def test_new_ledger_preserves_old_85_and_does_not_reset_daily(self):
        with tempfile.TemporaryDirectory() as d:
            old = Path(d)/'paper.sqlite3'; old.write_bytes(b'old-85-ledger')
            a = normalize_account(balance(),[],[],'testaccount')
            cfg, funding, ledger = resolve_funding(d,Config(),client=Client(a))
            self.assertEqual(cfg.initial_cash,1000)
            self.assertEqual(old.read_bytes(),b'old-85-ledger')
            a['total_equity_usdt']=1100
            cfg2, current, ledger2 = resolve_funding(d,Config(),client=Client(a))
            self.assertEqual(cfg2.initial_cash,1000)
            self.assertEqual(current['account']['total_equity_usdt'],1100)
            self.assertEqual(ledger,ledger2)

    def test_account_failure_blocks_forward_without_cash_fallback(self):
        snapshot={'source':'OKX_PUBLIC_REST','bar':'1Dutc','server_ms':0,'clock_ok':False,'captured_at':'2026-09-03T00:00:00Z','symbols':{}}
        with tempfile.TemporaryDirectory() as d, patch('research.northstar_crypto.runner.resolve_funding',side_effect=DataError('DEMO_API_ERROR:50111')), patch('research.northstar_crypto.runner.PublicOKX.capture',return_value=snapshot):
            _, a=run(d)
            self.assertEqual(a['paper']['status'],'BLOCKED_ACCOUNT')
            self.assertIsNone(a['config']['initial_cash'])
            self.assertFalse(list(Path(d).glob('*.sqlite3')))
            self.assertFalse(a['quality_checks']['account_funding_ready'])


if __name__=='__main__': unittest.main()
