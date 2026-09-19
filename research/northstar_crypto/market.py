"""Public GET-only OKX adapter. No credentials, trading SDK or private routes."""
from datetime import datetime, timezone
import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

DAY_MS = 86_400_000
ALLOWED = {'/api/v5/public/time', '/api/v5/public/instruments',
           '/api/v5/market/candles', '/api/v5/market/ticker'}


class DataError(ValueError):
    pass


class PublicOKX:
    def __init__(self, opener=urlopen):
        self.opener = opener
        self.receipts = []

    def get(self, path, **params):
        if path not in ALLOWED:
            raise DataError('PUBLIC_ROUTE_NOT_ALLOWED')
        url = 'https://www.okx.com' + path + '?' + urlencode(params)
        for attempt in range(1, 3):
            started = time.time()
            receipt = {'path': path, 'params': params, 'attempt': attempt}
            try:
                req = Request(url, headers={'User-Agent': 'NorthstarCryptoResearch/0.1'}, method='GET')
                with self.opener(req, timeout=15) as response:
                    payload = json.loads(response.read().decode('utf-8'))
                receipt.update(http_status=200, code=payload.get('code'))
                if payload.get('code') != '0':
                    raise DataError('OKX_BUSINESS_ERROR:' + str(payload.get('code')))
                if not isinstance(payload.get('data'), list) or not payload['data']:
                    raise DataError('EMPTY_RESULT')
                return payload
            except HTTPError as exc:
                receipt.update(error='HTTP_ERROR', http_status=exc.code)
                if attempt == 2 or exc.code not in {429, 500, 502, 503, 504}:
                    raise DataError('HTTP_' + str(exc.code)) from None
            except (TimeoutError, URLError) as exc:
                receipt['error'] = type(exc).__name__
                if attempt == 2:
                    raise DataError('NETWORK_' + type(exc).__name__) from None
            except (DataError, ValueError) as exc:
                receipt['error'] = str(exc)[:120]
                raise DataError(str(exc)) from None
            finally:
                receipt['elapsed_ms'] = round((time.time() - started) * 1000)
                self.receipts.append(receipt)
            time.sleep(0.5)

    def capture(self, config):
        local_before = int(time.time() * 1000)
        server_ms = int(self.get('/api/v5/public/time')['data'][0]['ts'])
        local_after = int(time.time() * 1000)
        clock_offset_ms = server_ms - (local_before + local_after) // 2
        clock_ok = abs(clock_offset_ms) <= config.max_clock_skew_seconds * 1000
        snapshot = {'schema_version': 1, 'source': 'OKX_PUBLIC_REST', 'bar': config.bar,
                    'server_ms': server_ms, 'clock_ok': clock_ok, 'clock_offset_ms': clock_offset_ms,
                    'captured_at': datetime.now(timezone.utc).isoformat(), 'symbols': {}}
        for symbol in config.symbols:
            result = {}
            try:
                result['candles'] = self.get('/api/v5/market/candles', instId=symbol,
                                             bar=config.bar, limit=config.history_limit)['data']
                result['instrument'] = self.get('/api/v5/public/instruments', instType='SPOT', instId=symbol)['data'][0]
                result['ticker'] = self.get('/api/v5/market/ticker', instId=symbol)['data'][0]
                result['quote_observed_ms'] = int(time.time() * 1000) + clock_offset_ms
                result['status'] = 'CAPTURED'
            except DataError as exc:
                result.update(status='DATA_ERROR', error=str(exc))
            snapshot['symbols'][symbol] = result
        snapshot['receipts'] = list(self.receipts)
        return snapshot


def validate_bars(rows, server_ms, *, minimum=120, require_latest=True):
    if not rows:
        raise DataError('EMPTY_CANDLES')
    records = []
    seen = set()
    for raw in rows:
        try:
            if len(raw) != 9 or str(raw[8]) not in {'0', '1'}:
                raise DataError('INVALID_CANDLE_SCHEMA')
            ts = int(raw[0])
            values = [float(x) for x in raw[1:8]]
            if ts in seen:
                raise DataError('DUPLICATE_TIMESTAMP')
            seen.add(ts)
            if ts % DAY_MS or ts > server_ms:
                raise DataError('INVALID_UTC_BOUNDARY')
            if not all(math.isfinite(x) for x in values):
                raise DataError('NONFINITE_CANDLE')
            o, h, low, c, vol = values[:5]
            if min(o, h, low, c) <= 0 or min(values[4:]) < 0 or low > min(o, c) or h < max(o, c) or h < low:
                raise DataError('INVALID_OHLCV')
            if str(raw[8]) == '0':
                continue
            if ts + DAY_MS > server_ms:
                raise DataError('FUTURE_CONFIRMED_CANDLE')
            records.append({'ts': ts, 'date': pd.to_datetime(ts, unit='ms', utc=True),
                            'open': o, 'high': h, 'low': low, 'close': c, 'volume': vol})
        except (TypeError, ValueError, IndexError) as exc:
            if isinstance(exc, DataError):
                raise
            raise DataError('CANDLE_PARSE_ERROR') from None
    records.sort(key=lambda x: x['ts'])
    if len(records) < minimum:
        raise DataError('INSUFFICIENT_CONFIRMED_BARS')
    if any(b['ts'] - a['ts'] != DAY_MS for a, b in zip(records, records[1:])):
        raise DataError('MISSING_DAILY_BAR')
    if require_latest and records[-1]['ts'] != (server_ms // DAY_MS - 1) * DAY_MS:
        raise DataError('STALE_DAILY_BARS')
    return pd.DataFrame(records).reset_index(drop=True)


def validate_instrument(symbol, instrument):
    if instrument.get('instId') != symbol or instrument.get('instType') != 'SPOT' or instrument.get('quoteCcy') != 'USDT' or instrument.get('state') != 'live':
        raise DataError('INSTRUMENT_NOT_ELIGIBLE')
    for key in ('lotSz', 'minSz', 'tickSz'):
        try:
            x = float(instrument[key])
            if not math.isfinite(x) or x <= 0:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise DataError('INVALID_INSTRUMENT_RULES') from None


def validate_quote(symbol, ticker, observed_ms, config, signal_close_ms):
    try:
        if ticker['instId'] != symbol:
            raise ValueError()
        ts = int(ticker['ts'])
        bid, ask = float(ticker['bidPx']), float(ticker['askPx'])
        if not all(math.isfinite(x) for x in (bid, ask)) or not 0 < bid <= ask:
            raise ValueError()
        if not signal_close_ms <= ts <= observed_ms + 5000:
            raise DataError('QUOTE_TIME_INVALID')
        if observed_ms - ts > config.quote_max_age_seconds * 1000:
            raise DataError('STALE_QUOTE')
        return {'bid': bid, 'ask': ask, 'ts': ts}
    except (KeyError, ValueError, TypeError) as exc:
        if isinstance(exc, DataError):
            raise
        raise DataError('QUOTE_INVALID') from None
