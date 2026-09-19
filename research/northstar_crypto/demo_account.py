"""OKX demo account authority. Credentials stay local; live mode is unavailable."""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .market import DataError

DEFAULT_CREDENTIALS = Path('D:/new_quant/2026-05-11-task-51/okx_engine/.env')
READ_ROUTES = {'/api/v5/account/balance', '/api/v5/account/positions',
               '/api/v5/trade/orders-pending', '/api/v5/trade/order',
               '/api/v5/public/instruments', '/api/v5/market/ticker',
               '/api/v5/account/config', '/api/v5/account/trade-fee'}


def number(value, label, *, positive=False):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise DataError('INVALID_ACCOUNT_NUMBER:' + label) from None
    if not math.isfinite(result) or result < 0 or (positive and result <= 0):
        raise DataError('INVALID_ACCOUNT_NUMBER:' + label)
    return result


class DemoAccount:
    def __init__(self, credentials=DEFAULT_CREDENTIALS, *, opener=urlopen):
        self.opener = opener
        self.receipts = []
        values = {}
        try:
            for line in Path(credentials).read_text(encoding='utf-8-sig').splitlines():
                if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    values[key.strip()] = value.strip().strip('"').strip("'")
        except OSError:
            raise DataError('DEMO_CREDENTIALS_UNAVAILABLE') from None
        if values.get('OKX_FLAG') != '1':
            raise DataError('DEMO_CREDENTIALS_REQUIRED')
        if not all(values.get(k) for k in ('OKX_API_KEY', 'OKX_SECRET_KEY', 'OKX_PASSPHRASE')):
            raise DataError('DEMO_CREDENTIALS_INCOMPLETE')
        self._credentials = values
        self.account_id = hashlib.sha256(values['OKX_API_KEY'].encode()).hexdigest()[:16]

    def get(self, route, **params):
        if route not in READ_ROUTES:
            raise DataError('DEMO_ROUTE_NOT_ALLOWED')
        return self._request('GET', route, params=params)

    def _request(self, method, route, *, params=None, body=None):
        self._validate_request(method, route, body)
        return self._transport(method, route, params=params, body=body)

    def _validate_request(self, method, route, body):
        if method != 'GET' or route not in READ_ROUTES:
            raise DataError('DEMO_READ_ONLY')

    def _transport(self, method, route, *, params=None, body=None):
        # Validate here too: no bypass through a direct transport invocation.
        self._validate_request(method, route, body)
        path = route + ('?' + urlencode(params) if params else '')
        try:
            started = time.monotonic()
            with self.opener(Request('https://www.okx.com/api/v5/public/time',
                headers={'User-Agent':'NorthstarCrypto/0.2'}), timeout=15) as response:
                clock = json.load(response)
            if clock.get('code') != '0' or time.monotonic() - started > 5:
                raise DataError('DEMO_TIME_UNAVAILABLE')
            ms = int(clock['data'][0]['ts'])
            self._clock_ms, self._clock_monotonic = ms, time.monotonic()
            stamp = datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
            encoded = '' if body is None else json.dumps(body, separators=(',', ':'))
            sig = base64.b64encode(hmac.new(self._credentials['OKX_SECRET_KEY'].encode(),
                (stamp + method + path + encoded).encode(), hashlib.sha256).digest()).decode()
            headers = {'OK-ACCESS-KEY':self._credentials['OKX_API_KEY'], 'OK-ACCESS-SIGN':sig,
                'OK-ACCESS-PASSPHRASE':self._credentials['OKX_PASSPHRASE'], 'OK-ACCESS-TIMESTAMP':stamp,
                'x-simulated-trading':'1', 'Content-Type':'application/json', 'User-Agent':'NorthstarCrypto/0.2'}
            if method == 'POST':
                headers['expTime'] = str(ms + 10000)
            req = Request('https://www.okx.com' + path, headers=headers, method=method,
                          data=encoded.encode() if body is not None else None)
            with self.opener(req, timeout=15) as response:
                payload = json.load(response)
        except HTTPError as exc:
            try:
                code = str(json.loads(exc.read()).get('code', exc.code))
            except (ValueError, OSError):
                code = str(exc.code)
            self.receipts.append({'mode':'demo','method':method,'route':route,'code':code})
            raise DataError('DEMO_API_ERROR:' + code) from None
        except (TimeoutError, URLError, KeyError, ValueError) as exc:
            if isinstance(exc, DataError):
                raise
            raise DataError('DEMO_REQUEST_FAILED:' + type(exc).__name__) from None
        self.receipts.append({'mode':'demo','method':method,'route':route,'code':payload.get('code')})
        if payload.get('code') != '0':
            raise DataError('DEMO_API_ERROR:' + str(payload.get('code')))
        if not isinstance(payload.get('data'), list):
            raise DataError('DEMO_RESPONSE_INVALID')
        return payload['data']

    def now_ms(self):
        if not hasattr(self, '_clock_ms'):
            raise DataError('DEMO_CLOCK_UNINITIALIZED')
        return self._clock_ms + int((time.monotonic() - self._clock_monotonic) * 1000)

    def capture(self):
        positions = self.get('/api/v5/account/positions')
        pending = self.get('/api/v5/trade/orders-pending')
        balance = self.get('/api/v5/account/balance')
        if len(balance) != 1:
            raise DataError('DEMO_BALANCE_INVALID')
        account = normalize_account(balance[0], positions, pending, self.account_id)
        account['receipts'] = list(self.receipts)
        return account


def normalize_account(balance, positions, pending, account_id):
    details = balance.get('details', [])
    if not details or len({r['ccy'] for r in details}) != len(details):
        raise DataError('DEMO_BALANCE_DETAILS_INVALID')
    usdt = next((r for r in details if r['ccy'] == 'USDT'), None)
    if usdt is None:
        raise DataError('USDT_VALUATION_UNAVAILABLE')
    usdt_eq = number(usdt.get('eq'), 'USDT.eq', positive=True)
    usdt_usd = number(usdt.get('eqUsd'), 'USDT.eqUsd', positive=True) / usdt_eq
    total_usd = number(balance.get('totalEq'), 'totalEq', positive=True)
    currencies = []
    for row in details:
        currencies.append({'currency':row['ccy'], 'equity':number(row.get('eq'), 'eq'),
            'equity_usd':number(row.get('eqUsd'), 'eqUsd'),
            'cash_balance':number(row.get('cashBal'), 'cashBal'),
            'available':number(row.get('availBal'), 'availBal'),
            'frozen':number(row.get('frozenBal') or '0', 'frozenBal'),
            'liability':number(row.get('liab') or '0', 'liab')})
    if abs(sum(r['equity_usd'] for r in currencies) - total_usd) > max(.01, total_usd * .001):
        raise DataError('DEMO_EQUITY_RECONCILIATION_FAILED')
    return {'source':'OKX_DEMO_ACCOUNT', 'mode':'demo', 'account_id':account_id,
        'capital_scope':'FULL_ACCOUNT_EQUITY', 'capital_allocation_fraction':1.0,
        'captured_at':datetime.now(timezone.utc).isoformat(),
        'balance_updated_ms':balance.get('uTime'),
        'total_equity_usd':total_usd, 'usdt_usd_rate':usdt_usd,
        'total_equity_usdt':total_usd / usdt_usd,
        'available_usdt':number(usdt.get('availBal'), 'USDT.availBal'),
        'cash_usdt':number(usdt.get('cashBal'), 'USDT.cashBal'),
        'currencies':currencies, 'positions':positions, 'pending_orders':pending,
        'valuation_basis':'totalEq USD / (USDT eqUsd / USDT eq)',
        'execution_environment':'OKX_DEMO_ONLY'}
