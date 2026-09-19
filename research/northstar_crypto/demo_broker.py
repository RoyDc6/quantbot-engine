"""Constrained OKX demo-only spot IOC broker; no live, margin or transfer route."""
from decimal import Decimal, InvalidOperation
import hashlib
import re

from .config import Config
from .demo_account import DemoAccount, READ_ROUTES
from .market import DataError, validate_instrument, validate_quote


class DemoBroker(DemoAccount):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.symbols = frozenset(Config().symbols)

    def _validate_request(self, method, route, body):
        if self._credentials.get('OKX_FLAG') != '1':
            raise DataError('DEMO_CREDENTIALS_REQUIRED')
        if method == 'GET' and route in READ_ROUTES:
            return
        if method != 'POST' or route not in {'/api/v5/trade/order','/api/v5/trade/cancel-order'}:
            raise DataError('DEMO_WRITE_ROUTE_NOT_ALLOWED')
        if not isinstance(body, dict) or body.get('instId') not in self.symbols:
            raise DataError('DEMO_SPOT_UNIVERSE_REQUIRED')
        if not re.fullmatch(r'nsc[a-zA-Z0-9]{1,29}', str(body.get('clOrdId',''))):
            raise DataError('OWNED_CLIENT_ORDER_ID_REQUIRED')
        if route.endswith('/cancel-order'):
            if set(body) != {'instId','clOrdId'}:
                raise DataError('DEMO_CANCEL_FIELDS_INVALID')
            return
        if set(body) != {'instId','clOrdId','tdMode','ordType','side','sz','px'}:
            raise DataError('DEMO_ORDER_FIELDS_INVALID')
        if body['tdMode'] != 'cash' or body['ordType'] != 'ioc' or body['side'] not in {'buy','sell'}:
            raise DataError('DEMO_CASH_IOC_ONLY')
        for key in ('sz','px'):
            try:
                value = Decimal(str(body[key]))
                if not value.is_finite() or value <= 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                raise DataError('INVALID_ORDER_' + key.upper()) from None

    def identify(self):
        rows = self.get('/api/v5/account/config')
        if len(rows) != 1 or not rows[0].get('uid'):
            raise DataError('DEMO_ACCOUNT_ID_UNAVAILABLE')
        # UID, rather than API-key identity, survives credential rotation.
        self.account_id = hashlib.sha256(('OKX_DEMO:' + rows[0]['uid']).encode()).hexdigest()[:20]
        if rows[0].get('acctLv') not in {'1','2'}:
            raise DataError('SPOT_CASH_ACCOUNT_MODE_REQUIRED')
        return {'account_id':self.account_id, 'account_level':rows[0]['acctLv'], 'mode':'demo'}

    def fee_rate(self, symbol='BTC-USDT'):
        rows = self.get('/api/v5/account/trade-fee', instType='SPOT', instId=symbol)
        if len(rows) != 1:
            raise DataError('DEMO_FEE_UNAVAILABLE')
        value = Decimal(str(rows[0].get('taker')))
        if not value.is_finite() or abs(value) > Decimal('.01'):
            raise DataError('DEMO_FEE_INVALID')
        return max(0.0, -float(value))

    def market_context(self, config, cutoff):
        instruments, quotes = {}, {}
        for symbol in config.symbols:
            rows = self.get('/api/v5/public/instruments', instType='SPOT', instId=symbol)
            if len(rows) != 1:
                raise DataError('DEMO_INSTRUMENT_UNAVAILABLE:' + symbol)
            validate_instrument(symbol, rows[0])
            instruments[symbol] = rows[0]
            quotes[symbol] = self.quote(symbol, config, cutoff)
        return instruments, quotes

    def quote(self, symbol, config, cutoff):
        rows = self.get('/api/v5/market/ticker', instId=symbol)
        if len(rows) != 1:
            raise DataError('DEMO_QUOTE_UNAVAILABLE')
        return validate_quote(symbol, rows[0], self.now_ms(), config, cutoff)

    def place_ioc(self, body):
        rows = self._request('POST','/api/v5/trade/order',body=body)
        if len(rows) != 1:
            raise DataError('DEMO_ORDER_ACK_INVALID')
        return rows[0]

    def order(self, symbol, clordid):
        rows = self.get('/api/v5/trade/order', instId=symbol, clOrdId=clordid)
        if len(rows) != 1:
            raise DataError('DEMO_ORDER_QUERY_INVALID')
        return rows[0]

    def cancel(self, symbol, clordid):
        return self._request('POST','/api/v5/trade/cancel-order',
                             body={'instId':symbol,'clOrdId':clordid})
