# -*- coding: utf-8 -*-
"""SQLite-backed order journal and market lease for live-confirmed orders.

The journal is intentionally only used by LIVE_CONFIRMED execution.  DRY_RUN
keeps the old lightweight log path and never creates or writes this database.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from .paths import OUTPUT_DIR
from .utils import to_futu_code


class OrderStatus(str, Enum):
    RESERVED = 'RESERVED'
    SUBMITTING = 'SUBMITTING'
    SUBMITTED = 'SUBMITTED'
    FILLED_PART = 'FILLED_PART'
    FILLED_ALL = 'FILLED_ALL'
    REJECTED = 'REJECTED'
    TIMEOUT = 'TIMEOUT'
    UNKNOWN = 'UNKNOWN'
    CANCELLED_PART = 'CANCELLED_PART'
    CANCELLED_ALL = 'CANCELLED_ALL'
    FAILED = 'FAILED'
    DISABLED = 'DISABLED'
    DELETED = 'DELETED'
    FILL_CANCELLED = 'FILL_CANCELLED'
    ABANDONED = 'ABANDONED'

    @classmethod
    def terminal_set(cls) -> set[str]:
        return {
            cls.FILLED_ALL.value,
            cls.REJECTED.value,
            cls.CANCELLED_ALL.value,
            cls.FAILED.value,
            cls.DISABLED.value,
            cls.DELETED.value,
            cls.ABANDONED.value,
        }

    @classmethod
    def uncertain_set(cls) -> set[str]:
        return {
            cls.SUBMITTING.value,
            cls.TIMEOUT.value,
            cls.UNKNOWN.value,
        }

    @classmethod
    def human_intervention_set(cls) -> set[str]:
        return {
            cls.FILLED_PART.value,
            cls.CANCELLED_PART.value,
            cls.FILL_CANCELLED.value,
            cls.TIMEOUT.value,
            cls.UNKNOWN.value,
        }

    @classmethod
    def blocking_set(cls) -> set[str]:
        return {status.value for status in cls} - cls.terminal_set()


FUTU_TO_QUANTBOT: dict[str, OrderStatus] = {
    'N/A': OrderStatus.UNKNOWN,
    'NONE': OrderStatus.UNKNOWN,
    'UNSUBMITTED': OrderStatus.SUBMITTING,
    'WAITING_SUBMIT': OrderStatus.SUBMITTING,
    'SUBMITTING': OrderStatus.SUBMITTING,
    'SUBMIT_FAILED': OrderStatus.REJECTED,
    'TIMEOUT': OrderStatus.TIMEOUT,
    'SUBMITTED': OrderStatus.SUBMITTED,
    'FILLED_PART': OrderStatus.FILLED_PART,
    'FILLED_ALL': OrderStatus.FILLED_ALL,
    'CANCELLING_PART': OrderStatus.CANCELLED_PART,
    'CANCELLING_ALL': OrderStatus.CANCELLED_ALL,
    'CANCELLED_PART': OrderStatus.CANCELLED_PART,
    'CANCELLED_ALL': OrderStatus.CANCELLED_ALL,
    'FAILED': OrderStatus.REJECTED,
    'DISABLED': OrderStatus.DISABLED,
    'DELETED': OrderStatus.DELETED,
    'FILL_CANCELLED': OrderStatus.FILL_CANCELLED,
}


FUTU_INT_TO_NAME: dict[int, str] = {
    -1: 'N/A',
    0: 'UNSUBMITTED',
    1: 'WAITING_SUBMIT',
    2: 'SUBMITTING',
    3: 'SUBMIT_FAILED',
    4: 'TIMEOUT',
    5: 'SUBMITTED',
    10: 'FILLED_PART',
    11: 'FILLED_ALL',
    12: 'CANCELLING_PART',
    13: 'CANCELLING_ALL',
    14: 'CANCELLED_PART',
    15: 'CANCELLED_ALL',
    21: 'FAILED',
    22: 'DISABLED',
    23: 'DELETED',
    24: 'FILL_CANCELLED',
}


def normalize_order_status(status: Any) -> OrderStatus:
    """Map a Futu SDK order_status value to the QuantBot state machine."""
    if isinstance(status, OrderStatus):
        return status
    if status is None or status == '':
        return OrderStatus.UNKNOWN
    if isinstance(status, (int, float)):
        return FUTU_TO_QUANTBOT.get(FUTU_INT_TO_NAME.get(int(status), ''), OrderStatus.UNKNOWN)
    text = str(status).strip()
    if text in FUTU_TO_QUANTBOT:
        return FUTU_TO_QUANTBOT[text]
    upper = text.upper()
    return FUTU_TO_QUANTBOT.get(upper, OrderStatus.UNKNOWN)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='milliseconds')


def _sqlite_now_expr() -> str:
    return "strftime('%Y-%m-%dT%H:%M:%f','now')"


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


@dataclass(frozen=True)
class OrderIdentity:
    run_date: str
    market: str
    symbol: str
    futu_code: str
    action: str
    qty: int
    price: float
    price_int: int
    intent_type: str
    intent_id: str
    broker_remark: str


def build_order_identity(order: dict[str, Any], market: str,
                         run_date: str | None = None) -> OrderIdentity:
    market = market.upper()
    run_date = run_date or datetime.now().strftime('%Y-%m-%d')
    symbol = str(order['symbol'])
    action = str(order['action']).upper()
    qty = int(order['qty'])
    price = round(float(order['price']), 3)
    price_int = int(round(price * 1000))
    intent_type = str(order.get('intent_type') or '').upper()
    if not intent_type:
        raise ValueError('intent_type is required for LIVE_CONFIRMED orders')
    futu_code = str(order.get('futu_code') or to_futu_code(symbol))
    raw = '|'.join([
        run_date, market, symbol, futu_code, action,
        str(qty), str(price_int), intent_type,
    ])
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    return OrderIdentity(
        run_date=run_date,
        market=market,
        symbol=symbol,
        futu_code=futu_code,
        action=action,
        qty=qty,
        price=price,
        price_int=price_int,
        intent_type=intent_type,
        intent_id=digest,
        broker_remark=f'QNT:v1:{digest[:32]}',
    )


class OrderJournal:
    """Durable journal with a market-level lease.

    A journal instance owns one SQLite connection.  Use one instance per market
    execution path.
    """

    def __init__(self, market: str, db_path: str | os.PathLike[str] | None = None,
                 owner: str | None = None, lease_seconds: int = 600):
        self.market = market.upper()
        base_path = Path(db_path) if db_path else OUTPUT_DIR / f'order_journal_{self.market}.db'
        base_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = base_path
        self.owner = owner or f'pid:{os.getpid()}'
        self.lease_seconds = int(lease_seconds)
        self.lease_token = ''
        self.conn = sqlite3.connect(
            str(self.db_path),
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA busy_timeout=30000')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                futu_code TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL')),
                qty INTEGER NOT NULL CHECK(qty > 0),
                price REAL NOT NULL CHECK(price > 0),
                price_int INTEGER NOT NULL,
                intent_type TEXT NOT NULL,
                intent_id TEXT NOT NULL UNIQUE,
                broker_remark TEXT NOT NULL UNIQUE,
                order_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                futu_status TEXT NOT NULL DEFAULT '',
                dealt_qty REAL NOT NULL DEFAULT 0,
                dealt_avg_price REAL NOT NULL DEFAULT 0,
                filled_at TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                post_fill_stop_applied INTEGER NOT NULL DEFAULT 0,
                post_fill_pos_init_applied INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_order_id_nonempty
            ON orders(order_id)
            WHERE order_id != '';

            CREATE INDEX IF NOT EXISTS idx_orders_market_status
            ON orders(market, status);

            CREATE INDEX IF NOT EXISTS idx_orders_broker_remark
            ON orders(broker_remark);

            CREATE TABLE IF NOT EXISTS market_leases (
                market TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                owner TEXT NOT NULL DEFAULT '',
                lease_token TEXT NOT NULL DEFAULT '',
                acquired_at TEXT NOT NULL DEFAULT '',
                renewed_at TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                market TEXT NOT NULL,
                action TEXT NOT NULL,
                intent_id TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_audit_market_created
            ON audit_log(market, created_at);
            """
        )

    @contextmanager
    def _transaction(self):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            yield self.conn
            self.conn.execute('COMMIT')
        except Exception:
            self.conn.execute('ROLLBACK')
            raise

    def _audit(self, conn: sqlite3.Connection, action: str,
               intent_id: str = '', detail: str = '') -> None:
        conn.execute(
            """
            INSERT INTO audit_log(created_at, market, action, intent_id, detail)
            VALUES ({now}, ?, ?, ?, ?)
            """.format(now=_sqlite_now_expr()),
            (self.market, action, intent_id, detail),
        )

    def acquire_lease(self) -> str:
        token = secrets.token_hex(16)
        with self._transaction() as conn:
            row = conn.execute(
                'SELECT status FROM market_leases WHERE market = ?',
                (self.market,),
            ).fetchone()
            if row and row['status'] == 'ACTIVE':
                self._audit(conn, 'lease_active_block', detail=self.owner)
                return ''
            conn.execute(
                """
                INSERT INTO market_leases(
                    market, status, owner, lease_token,
                    acquired_at, renewed_at, expires_at, updated_at
                )
                VALUES (
                    ?, 'ACTIVE', ?, ?,
                    {now}, {now}, datetime({now}, ?), {now}
                )
                ON CONFLICT(market) DO UPDATE SET
                    status='ACTIVE',
                    owner=excluded.owner,
                    lease_token=excluded.lease_token,
                    acquired_at={now},
                    renewed_at={now},
                    expires_at=datetime({now}, ?),
                    updated_at={now}
                """.format(now=_sqlite_now_expr()),
                (self.market, self.owner, token,
                 f'+{self.lease_seconds} seconds',
                 f'+{self.lease_seconds} seconds'),
            )
            self._audit(conn, 'acquire_lease', detail=self.owner)
            self.lease_token = token
            return token

    def release_lease(self) -> bool:
        if not self.lease_token:
            return False
        with self._transaction() as conn:
            cur = conn.execute(
                """
                UPDATE market_leases
                SET status='RELEASED', updated_at={now}
                WHERE market=? AND status='ACTIVE' AND lease_token=? AND owner=?
                """.format(now=_sqlite_now_expr()),
                (self.market, self.lease_token, self.owner),
            )
            if cur.rowcount == 1:
                self._audit(conn, 'release_lease', detail=self.owner)
                self.lease_token = ''
                return True
            return False

    def renew_lease(self) -> bool:
        if not self.lease_token:
            return False
        with self._transaction() as conn:
            cur = conn.execute(
                """
                UPDATE market_leases
                SET renewed_at={now}, expires_at=datetime({now}, ?), updated_at={now}
                WHERE market=? AND status='ACTIVE' AND lease_token=? AND owner=?
                """.format(now=_sqlite_now_expr()),
                (f'+{self.lease_seconds} seconds', self.market, self.lease_token, self.owner),
            )
            if cur.rowcount == 1:
                self._audit(conn, 'renew_lease', detail=self.owner)
                return True
            return False

    def force_release_expired_lease(self, owner: str, token: str) -> bool:
        with self._transaction() as conn:
            cur = conn.execute(
                """
                UPDATE market_leases
                SET status='RELEASED', updated_at={now}
                WHERE market=? AND status='ACTIVE' AND owner=? AND lease_token=?
                  AND datetime(expires_at) < datetime({now})
                """.format(now=_sqlite_now_expr()),
                (self.market, owner, token),
            )
            if cur.rowcount == 1:
                self._audit(conn, 'force_release_expired_lease', detail=owner)
                return True
            return False

    def _assert_lease_valid(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            """
            SELECT 1 FROM market_leases
            WHERE market=? AND status='ACTIVE' AND owner=? AND lease_token=?
            """,
            (self.market, self.owner, self.lease_token),
        ).fetchone()
        if row is None:
            raise RuntimeError(f'LIVE journal lease is not active for {self.market}')

    def reserve_order(self, order: dict[str, Any]) -> dict[str, Any]:
        identity = build_order_identity(order, self.market)
        with self._transaction() as conn:
            self._assert_lease_valid(conn)
            existing = conn.execute(
                'SELECT * FROM orders WHERE intent_id=?',
                (identity.intent_id,),
            ).fetchone()
            if existing:
                self._audit(conn, 'reserve_duplicate', identity.intent_id, existing['status'])
                return _row_to_dict(existing)  # type: ignore[return-value]

            conn.execute(
                """
                INSERT INTO orders(
                    run_date, market, symbol, futu_code, action, qty, price,
                    price_int, intent_type, intent_id, broker_remark, status,
                    reason, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {now}, {now})
                """.format(now=_sqlite_now_expr()),
                (
                    identity.run_date, identity.market, identity.symbol,
                    identity.futu_code, identity.action, identity.qty,
                    identity.price, identity.price_int, identity.intent_type,
                    identity.intent_id, identity.broker_remark,
                    OrderStatus.RESERVED.value, str(order.get('reason', '')),
                ),
            )
            self._audit(conn, 'reserve_order', identity.intent_id, identity.broker_remark)
            row = conn.execute(
                'SELECT * FROM orders WHERE intent_id=?',
                (identity.intent_id,),
            ).fetchone()
            return _row_to_dict(row)  # type: ignore[return-value]

    def mark_submitting(self, intent_id: str) -> None:
        with self._transaction() as conn:
            self._assert_lease_valid(conn)
            cur = conn.execute(
                """
                UPDATE orders
                SET status=?, updated_at={now}
                WHERE intent_id=? AND status=?
                """.format(now=_sqlite_now_expr()),
                (OrderStatus.SUBMITTING.value, intent_id, OrderStatus.RESERVED.value),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f'Cannot move {intent_id} from RESERVED to SUBMITTING')
            self._audit(conn, 'mark_submitting', intent_id)

    def record_place_result(self, intent_id: str, result: Any) -> dict[str, Any]:
        status = normalize_order_status(getattr(result, 'status', None))
        order_id = str(getattr(result, 'order_id', '') or '')
        futu_status = str(getattr(result, 'futu_status', '') or '')
        dealt_qty = float(getattr(result, 'dealt_qty', 0) or 0)
        dealt_avg_price = float(getattr(result, 'dealt_avg_price', 0) or 0)
        filled_at = str(getattr(result, 'filled_at', '') or '')
        if status == OrderStatus.FILLED_ALL and not filled_at:
            filled_at = _utc_now_iso()

        with self._transaction() as conn:
            self._assert_lease_valid(conn)
            cur = conn.execute(
                """
                UPDATE orders
                SET status=?, order_id=?, futu_status=?, dealt_qty=?,
                    dealt_avg_price=?, filled_at=?, updated_at={now}
                WHERE intent_id=? AND status=?
                """.format(now=_sqlite_now_expr()),
                (
                    status.value, order_id, futu_status, dealt_qty,
                    dealt_avg_price, filled_at, intent_id,
                    OrderStatus.SUBMITTING.value,
                ),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f'Cannot record broker result for {intent_id}')
            self._audit(conn, 'record_place_result', intent_id, status.value)
            return self.get_order(intent_id) or {}

    def update_from_futu_order(self, intent_id: str, futu_order: dict[str, Any]) -> dict[str, Any]:
        status = normalize_order_status(futu_order.get('order_status'))
        order_id = str(futu_order.get('order_id', '') or '')
        futu_status = str(futu_order.get('order_status', '') or '')
        dealt_qty = float(futu_order.get('dealt_qty', 0) or 0)
        dealt_avg_price = float(futu_order.get('dealt_avg_price', 0) or 0)
        filled_at = str(futu_order.get('updated_time', '') or futu_order.get('create_time', '') or '')
        if status == OrderStatus.FILLED_ALL and not filled_at:
            filled_at = _utc_now_iso()
        with self._transaction() as conn:
            self._assert_lease_valid(conn)
            conn.execute(
                """
                UPDATE orders
                SET status=?, order_id=CASE WHEN ? != '' THEN ? ELSE order_id END,
                    futu_status=?, dealt_qty=?, dealt_avg_price=?,
                    filled_at=CASE WHEN ? != '' THEN ? ELSE filled_at END,
                    updated_at={now}
                WHERE intent_id=?
                """.format(now=_sqlite_now_expr()),
                (
                    status.value, order_id, order_id, futu_status, dealt_qty,
                    dealt_avg_price, filled_at, filled_at, intent_id,
                ),
            )
            self._audit(conn, 'reconcile_order', intent_id, status.value)
            return self.get_order(intent_id) or {}

    def mark_abandoned(self, intent_id: str) -> None:
        with self._transaction() as conn:
            self._assert_lease_valid(conn)
            cur = conn.execute(
                """
                UPDATE orders
                SET status=?, updated_at={now}
                WHERE intent_id=? AND status=?
                """.format(now=_sqlite_now_expr()),
                (OrderStatus.ABANDONED.value, intent_id, OrderStatus.RESERVED.value),
            )
            if cur.rowcount == 1:
                self._audit(conn, 'mark_abandoned', intent_id)

    def get_order(self, intent_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            'SELECT * FROM orders WHERE intent_id=?',
            (intent_id,),
        ).fetchone()
        return _row_to_dict(row)

    def unresolved_orders(self) -> list[dict[str, Any]]:
        terminal = tuple(OrderStatus.terminal_set())
        placeholders = ','.join('?' for _ in terminal)
        rows = self.conn.execute(
            f"""
            SELECT * FROM orders
            WHERE market=? AND status NOT IN ({placeholders})
            ORDER BY created_at, id
            """,
            (self.market, *terminal),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]  # type: ignore[list-item]

    def has_blocking_orders(self) -> bool:
        return bool(self.unresolved_orders())

    def reconcile(self, adapter: Any, stale_reserved_seconds: int = 3600) -> list[dict[str, Any]]:
        unresolved = self.unresolved_orders()
        if not unresolved:
            return []
        if adapter is None:
            raise RuntimeError('Cannot reconcile unresolved orders without adapter')
        qr = adapter.get_order_list(market=self.market)
        futu_orders = qr.require(f'{self.market} order list') if hasattr(qr, 'require') else qr
        if futu_orders is None:
            raise RuntimeError(f'{self.market} order list query returned None')

        by_order_id = {
            str(o.get('order_id', '')): o for o in futu_orders
            if str(o.get('order_id', '') or '')
        }
        by_remark = {
            (str(o.get('code', '') or ''), str(o.get('remark', '') or '')): o
            for o in futu_orders
        }

        changed: list[dict[str, Any]] = []
        now_ts = datetime.now(timezone.utc).replace(tzinfo=None)
        for entry in unresolved:
            match = None
            if entry.get('order_id'):
                match = by_order_id.get(str(entry['order_id']))
            if match is None:
                match = by_remark.get((entry['futu_code'], entry['broker_remark']))
            if match is not None:
                changed.append(self.update_from_futu_order(entry['intent_id'], match))
                continue
            if entry['status'] == OrderStatus.RESERVED.value and not entry.get('order_id'):
                try:
                    created = datetime.fromisoformat(entry['created_at'])
                    stale = (now_ts - created).total_seconds() > stale_reserved_seconds
                except Exception:
                    stale = False
                if stale:
                    self.mark_abandoned(entry['intent_id'])
                    changed.append(self.get_order(entry['intent_id']) or entry)
        return changed

    def apply_stop_side_effects(self, risk_manager: Any) -> int:
        if risk_manager is None:
            return 0
        rows = self.conn.execute(
            """
            SELECT * FROM orders
            WHERE market=? AND intent_type='STOP_SELL' AND status=?
              AND post_fill_stop_applied=0
            ORDER BY created_at, id
            """,
            (self.market, OrderStatus.FILLED_ALL.value),
        ).fetchall()
        applied = 0
        for row in rows:
            entry = _row_to_dict(row)
            assert entry is not None
            risk_manager.confirm_stop(entry['futu_code'], entry.get('filled_at') or _utc_now_iso())
            with self._transaction() as conn:
                self._assert_lease_valid(conn)
                cur = conn.execute(
                    """
                    UPDATE orders
                    SET post_fill_stop_applied=1, updated_at={now}
                    WHERE intent_id=? AND post_fill_stop_applied=0
                    """.format(now=_sqlite_now_expr()),
                    (entry['intent_id'],),
                )
                if cur.rowcount == 1:
                    self._audit(conn, 'confirm_stop_applied', entry['intent_id'])
                    applied += 1
        return applied

    def validate_futu_sdk_mapping(self, ft_module: Any) -> None:
        """Fail if the installed SDK has an unmapped OrderStatus value."""
        sdk_names: Iterable[str] = ft_module.OrderStatus().load_dic().keys()
        missing = set(sdk_names) - set(FUTU_TO_QUANTBOT)
        if missing:
            raise RuntimeError(f'Unmapped Futu OrderStatus values: {sorted(missing)}')
