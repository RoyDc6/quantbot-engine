from datetime import datetime, timezone
import json
import time
import sqlite3
from pathlib import Path
from uuid import uuid4
from dataclasses import replace

from .config import Config
from .events import read_observations
from .funding import resolve_funding
from .io_utils import digest, write_json
from .market import DataError, PublicOKX, validate_bars, validate_instrument, validate_quote
from .portfolio import PaperLedger
from .report import render
from .strategy import Strategy


def run(output, *, snapshot=None, observations=None, comparison=False, config=None, account_client=None, execute_demo=False):
    output = Path(output)
    config = config or Config()
    if execute_demo and snapshot is not None:
        raise ValueError('ARCHIVED_INPUT_CANNOT_EXECUTE_ORDERS')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:10]
    directory = output / 'runs' / run_id
    directory.mkdir(parents=True, exist_ok=False)
    live_capture = snapshot is None
    funding = {'status':'REPLAY_ONLY', 'source':'EXPLICIT_REPLAY_CAPITAL', 'okx_orders_enabled':False}
    ledger_path = None
    if live_capture:
        try:
            if config.initial_cash is not None:
                raise DataError('FORWARD_CAPITAL_MUST_COME_FROM_DEMO_ACCOUNT')
            if execute_demo:
                from .demo_broker import DemoBroker
                account_client = account_client or DemoBroker()
                identity = account_client.identify()
                account = account_client.capture()
                funding = {'status':'READY', 'source':'OKX_DEMO_ACCOUNT_FULL_EQUITY',
                           'okx_orders_enabled':True, 'capital_allocation_fraction':1.0,
                           'account':account, 'identity':identity,
                           'capital_policy':'CURRENT_FULL_EQUITY_NO_LOCAL_SEED'}
            else:
                config, funding, ledger_path = resolve_funding(output, config, client=account_client)
        except (DataError, ValueError, OSError, sqlite3.Error) as exc:
            funding = {'status':'BLOCKED_ACCOUNT', 'source':'OKX_DEMO_ACCOUNT_FULL_EQUITY',
                       'error':str(exc), 'okx_orders_enabled':False}
    strategy = Strategy(config)
    write_json(directory / 'funding.json', funding)
    if snapshot is None:
        try:
            snapshot = PublicOKX().capture(config)
        except DataError as exc:
            snapshot = {'schema_version': 1, 'source': 'OKX_PUBLIC_REST', 'bar': config.bar,
                        'server_ms': 0, 'clock_ok': False, 'captured_at': datetime.now(timezone.utc).isoformat(),
                        'symbols': {}, 'error': str(exc)}
    write_json(directory / 'input.json', snapshot)
    signals, bars_by_symbol, instruments, quotes = [], {}, {}, {}
    quote_errors = {}
    for symbol in config.symbols:
        row = snapshot.get('symbols', {}).get(symbol, {})
        try:
            if snapshot.get('source') != 'OKX_PUBLIC_REST' or snapshot.get('bar') != config.bar:
                raise DataError('INVALID_SOURCE_CONTRACT')
            if not snapshot.get('clock_ok'):
                raise DataError(snapshot.get('error', 'CLOCK_CHECK_FAILED'))
            if row.get('status') != 'CAPTURED':
                raise DataError(row.get('error', 'CAPTURE_MISSING'))
            bars = validate_bars(row['candles'], snapshot['server_ms'], minimum=config.warmup)
            validate_instrument(symbol, row['instrument'])
            signal = strategy.signal(bars, symbol)
            signal['bars'] = len(bars)
            signal['input_hash'] = digest({'candles': [r for r in row['candles'] if str(r[8]) == '1'], 'bar': config.bar})
            signals.append(signal)
            bars_by_symbol[symbol] = bars
            instruments[symbol] = row['instrument']
            try:
                quotes[symbol] = validate_quote(symbol, row['ticker'], row['quote_observed_ms'], config, signal['signal_close_ms'])
            except (DataError, KeyError) as exc:
                quote_errors[symbol] = str(exc)
        except (DataError, ValueError, KeyError) as exc:
            signals.append({'symbol': symbol, 'data_status': 'DATA_ERROR', 'signal_status': 'BLOCKED',
                            'action': None, 'raw_fraction': None, 'error': str(exc)[:200]})
    valid = [s for s in signals if s['data_status'] == 'VALID']
    complete = len(valid) == len(config.symbols)
    paper = {'status': 'ARCHIVED_INPUT_NO_FORWARD_WRITE', 'orders': [], 'actual_orders': 0}
    state = None
    execution = {'status':'NOT_REQUESTED', 'orders':[], 'new_orders':0, 'real_orders':0}
    if live_capture:
        # Revalidate at application time, not the earlier per-symbol fetch time.
        application_ms = int(time.time() * 1000) + snapshot.get('clock_offset_ms', 0)
        for signal in valid:
            symbol = signal['symbol']
            try:
                quotes[symbol] = validate_quote(symbol, snapshot['symbols'][symbol]['ticker'], application_ms,
                                                config, signal['signal_close_ms'])
            except (DataError, KeyError) as exc:
                quote_errors[symbol] = str(exc)
        if funding['status'] != 'READY':
            paper.update(status='BLOCKED_ACCOUNT', error=funding.get('error', 'ACCOUNT_FUNDING_REQUIRED'))
        elif not complete or (quote_errors and not execute_demo):
            paper.update(status='BLOCKED_DATA', error='行情或报价不完整，本轮账本未更新。')
        else:
            try:
                if execute_demo:
                    from .demo_execution import DemoExecutor
                    execution = DemoExecutor(account_client, config).apply(valid)
                    # Demo quotes are refreshed and validated inside the execution boundary.
                    quote_errors = {}
                    paper = {'status':'NOT_USED_DEMO_EXCHANGE', 'orders':[], 'actual_orders':0}
                    if execution.get('current_account') or execution.get('account_after'):
                        funding['account'] = execution.get('current_account') or execution['account_after']
                else:
                    ledger = PaperLedger(ledger_path, config)
                    state, paper = ledger.apply(valid, quotes, instruments)
            except (DataError, ValueError, OSError, sqlite3.Error) as exc:
                paper.update(status='BLOCKED_ACCOUNTING', error=str(exc))
                if execute_demo:
                    execution.update(status='BLOCKED', error=str(exc))
    diagnostics = {}
    if comparison and complete:
        from .replay import compare
        try:
            comparison_config = replace(config, initial_cash=funding['account']['total_equity_usdt']) if execute_demo and funding.get('account') else config
            if comparison_config.initial_cash is None:
                raise ValueError('REPLAY_CAPITAL_REQUIRED_NO_DEFAULT')
            diagnostics = compare(bars_by_symbol, instruments, Strategy(comparison_config), comparison_config)
        except ValueError as exc:
            diagnostics = {'error': str(exc)}
    counts = {action: sum(s.get('action') == action for s in valid) for action in ('BUY', 'SELL', 'HOLD')}
    counts['DATA_ERROR'] = len(signals) - len(valid)
    try:
        llm = read_observations(observations, min((s['signal_close_ms'] for s in valid), default=0), config.symbols)
    except (OSError, ValueError) as exc:
        llm = {'status': 'INPUT_ERROR', 'position_effect': 0, 'accepted': [], 'error': str(exc)}
    quality_checks = {'data_complete': complete, 'quotes_valid': not quote_errors,
                      'account_funding_ready': not live_capture or funding['status'] == 'READY',
                      'paper_not_blocked': not paper['status'].startswith('BLOCKED'),
                      'paper_risk_resolved': paper.get('risk_resolved', True),
                      'requested_comparison_complete': not comparison or bool(diagnostics.get('results')),
                      'llm_input_readable': llm['status'] != 'INPUT_ERROR'}
    if execute_demo:
        quality_checks.update(demo_execution_terminal=execution['status'] in {'APPLIED','RECOVERED'},
                              demo_balances_reconciled=execution.get('reconciliation', {}).get('status') == 'PASS',
                              demo_risk_resolved=execution.get('risk_resolved', False),
                              demo_requested_orders_filled=all(o['status']=='FILLED' for o in execution['orders']),
                              demo_no_dispatch_error=not execution.get('error'))
    artifact = {'schema_version': 3, 'run_id': run_id, 'mode': 'OKX_DEMO_FORWARD' if execute_demo else ('DEMO_ACCOUNT_FUNDED_LOCAL_PAPER' if live_capture else 'SNAPSHOT_REPLAY'),
                'status': 'COMPLETE' if all(quality_checks.values()) else 'PARTIAL',
                'validation_status': 'NOT_EVALUATED', 'execution_enabled': execute_demo,
                'live_execution_enabled': False, 'execution':execution,
                'snapshot_captured_at': snapshot['captured_at'], 'snapshot_hash': digest(snapshot),
                'vendor_hash': strategy.vendor_hash, 'config': config.payload(), 'signals': signals,
                'counts': counts, 'quote_errors': quote_errors, 'paper': paper, 'paper_state': state, 'funding':funding,
                'comparison': diagnostics, 'llm': llm, 'quality_checks': quality_checks}
    write_json(directory / 'artifact.json', artifact)
    write_json(directory / 'funding.json', funding)
    (directory / 'Crypto日报.md').write_text(render(artifact), encoding='utf-8')
    import hashlib
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir() if p.is_file()}
    write_json(directory / 'manifest.json', {'run_id': run_id, 'files': hashes})
    pointer = {'run_id': run_id, 'directory': str(directory), 'status': artifact['status'],
               'report': str(directory / 'Crypto日报.md'), 'artifact': str(directory / 'artifact.json')}
    write_json(output / ('latest.json' if live_capture else 'latest_replay.json'), pointer)
    if artifact['status'] == 'COMPLETE' and live_capture:
        write_json(output / 'latest_valid.json', pointer)
    return pointer, artifact
