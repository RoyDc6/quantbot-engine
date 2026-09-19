"""Seed a separate local simulation from all demo equity, preserving its history."""
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

from .demo_account import DemoAccount
from .io_utils import canonical
from .market import DataError


def resolve_funding(output, config, *, client=None):
    """Read fresh account evidence every run; never reset an existing paper P&L.

    Account NAV is the full equity valuation, not just available USDT. The seed
    is an all-cash local research portfolio, not an assertion that the account's
    other currencies were converted or transferred at OKX.
    """
    account = (client or DemoAccount()).capture()
    if account['source'] != 'OKX_DEMO_ACCOUNT' or account['mode'] != 'demo':
        raise DataError('DEMO_ACCOUNT_SOURCE_REQUIRED')
    ledger_path = Path(output) / ('paper_okx_demo_' + account['account_id'] + '.sqlite3')
    with closing(sqlite3.connect(ledger_path, timeout=15)) as con, con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('CREATE TABLE IF NOT EXISTS funding_seed (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
        row = con.execute('SELECT payload FROM funding_seed WHERE id=1').fetchone()
        if row:
            seed = json.loads(row[0])
            if seed['account_id'] != account['account_id'] or seed['capital_allocation_fraction'] != 1.0:
                raise DataError('FUNDING_IDENTITY_CONFLICT')
        else:
            seed = {k: account[k] for k in ('account_id', 'source', 'mode', 'captured_at',
                'total_equity_usdt', 'total_equity_usd', 'available_usdt', 'capital_scope',
                'capital_allocation_fraction', 'valuation_basis')}
            con.execute('INSERT INTO funding_seed VALUES (1,?)', (canonical(seed),))
    resolved = replace(config, initial_cash=seed['total_equity_usdt'])
    funding = {'status':'READY', 'source':'OKX_DEMO_ACCOUNT_FULL_EQUITY',
        'capital_allocation_fraction':1.0, 'account':account, 'seed':seed,
        'initial_capital_usdt':seed['total_equity_usdt'], 'ledger':str(ledger_path),
        'execution_mode':'LOCAL_PAPER_ACCOUNT_FUNDED',
        'capital_semantics':'All demo equity at ledger inception; current account equity shown separately.',
        'okx_orders_enabled':False}
    return resolved, funding, ledger_path
