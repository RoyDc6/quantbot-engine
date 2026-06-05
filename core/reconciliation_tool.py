# -*- coding: utf-8 -*-
"""Read-only Phase F2-SEC reconciliation helper.

This tool never places, cancels, amends, or updates orders.  It reads the local
SQLite journal and, when requested, compares unresolved entries with Futu
SIMULATE order_list_query() results by order_id and full broker_remark.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.futu_adapter import FutuAdapter
from core.order_journal import OrderStatus
from core.paths import OUTPUT_DIR


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f'journal DB not found: {db_path}')
    conn = sqlite3.connect(f'file:{db_path.as_posix()}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load_unresolved(conn: sqlite3.Connection, market: str) -> list[dict]:
    terminal = tuple(OrderStatus.terminal_set())
    placeholders = ','.join('?' for _ in terminal)
    rows = conn.execute(
        f"""
        SELECT * FROM orders
        WHERE market=? AND status NOT IN ({placeholders})
        ORDER BY created_at, id
        """,
        (market.upper(), *terminal),
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def _compare_with_broker(market: str, unresolved: list[dict]) -> dict:
    adapter = FutuAdapter()
    qr = adapter.get_order_list(market=market)
    futu_orders = qr.require(f'{market} order_list_query')
    by_order_id = {
        str(order.get('order_id', '') or ''): order
        for order in futu_orders
        if str(order.get('order_id', '') or '')
    }
    by_remark = {
        (str(order.get('code', '') or ''), str(order.get('remark', '') or '')): order
        for order in futu_orders
    }
    matches = []
    missing = []
    for entry in unresolved:
        match = None
        if entry.get('order_id'):
            match = by_order_id.get(str(entry['order_id']))
        if match is None:
            match = by_remark.get((entry['futu_code'], entry['broker_remark']))
        if match is None:
            missing.append(entry)
        else:
            matches.append({'journal': entry, 'broker': match})
    return {
        'broker_orders': len(futu_orders),
        'matches': matches,
        'missing_in_broker': missing,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only F2-SEC reconciliation helper')
    parser.add_argument('--market', choices=['HK', 'US'], required=True)
    parser.add_argument('--db-path', default='')
    parser.add_argument('--with-broker', action='store_true',
                        help='Read Futu SIMULATE order_list_query and compare; still no writes')
    args = parser.parse_args(argv)

    market = args.market.upper()
    db_path = Path(args.db_path) if args.db_path else OUTPUT_DIR / f'order_journal_{market}.db'
    conn = _connect_readonly(db_path)
    try:
        unresolved = _load_unresolved(conn, market)
    finally:
        conn.close()

    report = {
        'market': market,
        'db_path': str(db_path),
        'unresolved_count': len(unresolved),
        'unresolved': unresolved,
    }
    if args.with_broker:
        report['broker_compare'] = _compare_with_broker(market, unresolved)

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 1 if unresolved else 0


if __name__ == '__main__':
    raise SystemExit(main())
