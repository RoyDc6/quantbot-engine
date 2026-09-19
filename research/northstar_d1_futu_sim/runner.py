"""Translate immutable Northstar-D1 artifacts into Futu SIMULATE orders.

The Northstar model remains frozen and research-only.  This sibling package is
an external forward-execution harness.  It never permits TrdEnv.REAL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.futu_adapter import FutuAdapter
from core.order_executor import OrderExecutor
from core.order_journal import OrderJournal, OrderStatus


ROOT = Path(__file__).resolve().parent
NORTHSTAR_OUTPUT = ROOT.parent / "northstar_d1" / "output"
RUNTIME = ROOT / "runtime"
RECEIPTS = ROOT / "receipts"
CONSUMED = RUNTIME / "consumed"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def select_latest_artifact(market: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    market = market.upper()
    output = NORTHSTAR_OUTPUT / market.lower()
    candidates: list[tuple[str, str, Path, dict[str, Any], dict[str, Any]]] = []
    for path in output.glob("*_northstar_d1_*.json"):
        if path.name.endswith(".manifest.json"):
            continue
        try:
            payload = _read_json(path)
            manifest_path = path.with_name(path.stem + ".manifest.json")
            manifest = _read_json(manifest_path)
        except (OSError, ValueError):
            continue
        if (
            payload.get("market") != market
            or payload.get("run_verdict") != "RUN_PASS"
            or not payload.get("data_ready")
            or not payload.get("freshness_ready")
            or manifest.get("run_verdict") != "RUN_PASS"
            or manifest.get("sha256", {}).get("json") != _sha256(path)
        ):
            continue
        candidates.append((
            str(payload.get("signal_asof") or ""),
            str(payload.get("generated_at") or ""),
            path,
            payload,
            manifest,
        ))
    if not candidates:
        raise RuntimeError(f"{market} has no hash-verified RUN_PASS artifact")
    _, _, path, payload, manifest = max(candidates, key=lambda x: (x[0], x[1]))
    return path, payload, manifest


def _position_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("symbol")): row for row in rows}


def build_orders(
    payload: dict[str, Any],
    account: dict[str, Any],
    positions: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create position-aware deltas from Northstar exposure semantics."""
    market = str(payload["market"]).upper()
    pos = _position_map(positions)
    orders: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    total_assets = float(account.get("total_assets") or 0)
    run_id = str((payload.get("run") or {}).get("run_id") or "")
    signal_asof = str(payload.get("signal_asof") or "")
    model_version = str(payload.get("model_version") or "UNKNOWN")

    if total_assets <= 0:
        raise RuntimeError(f"{market} simulated account total_assets is not positive")

    for intent in payload.get("paper_intents") or []:
        symbol = str(intent["symbol"])
        action = str(intent["action"]).upper()
        fraction = float(intent.get("risk_capped_fraction") or 0)
        quote = quotes.get(symbol) or {}
        price = float(quote.get("price") or 0)
        lot_size = int(quote.get("lot_size") or (100 if market == "HK" else 1))
        current = pos.get(symbol) or {}
        current_qty = int(float(current.get("qty") or 0))
        can_sell_qty = int(float(current.get("can_sell_qty") or 0))
        reason_base = f"Northstar-D1 {signal_asof} {run_id}"

        if price <= 0 or lot_size <= 0:
            skipped.append({"symbol": symbol, "action": action, "reason": "QUOTE_OR_LOT_INVALID"})
            continue

        if action == "BUY" and intent.get("fraction_semantics") == "desired_long_exposure":
            target_qty = math.floor((total_assets * fraction) / price / lot_size) * lot_size
            qty = math.floor(max(0, target_qty - current_qty) / lot_size) * lot_size
            reason = f"{reason_base}; target={fraction:.4f}; current_qty={current_qty}; target_qty={target_qty}"
        elif action == "SELL" and intent.get("fraction_semantics") == "fraction_of_existing_position_to_reduce":
            qty = math.floor(min(can_sell_qty, current_qty * fraction) / lot_size) * lot_size
            reason = f"{reason_base}; reduce={fraction:.4f}; current_qty={current_qty}; can_sell={can_sell_qty}"
        else:
            skipped.append({"symbol": symbol, "action": action, "reason": "SEMANTICS_NOT_EXECUTABLE"})
            continue

        if qty <= 0:
            skipped.append({"symbol": symbol, "action": action, "reason": "NO_POSITION_DELTA_OR_BELOW_LOT"})
            continue

        identity_tag = f"NORTHSTAR_D1_{model_version}_{signal_asof}_{action}".replace(".", "_").replace("-", "_")
        orders.append({
            "symbol": symbol,
            "action": action,
            "qty": qty,
            "price": price,
            "lot_size": lot_size,
            "intent_type": identity_tag,
            "reason": reason,
        })
    return orders, skipped


def _journal_factory(market: str) -> OrderJournal:
    return OrderJournal(
        market,
        db_path=RUNTIME / f"northstar_d1_futu_sim_{market.lower()}.db",
        owner=f"northstar-d1-futu-sim:{os.getpid()}",
    )


def _source_marker(market: str, run_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in run_id)
    return CONSUMED / market.lower() / f"{safe}.json"


def _reserve_source(market: str, run_id: str, source_sha256: str) -> tuple[Path, bool]:
    marker = _source_marker(market, run_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "market": market,
        "run_id": run_id,
        "source_sha256": source_sha256,
        "status": "RESERVED_FAIL_CLOSED",
        "reserved_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with marker.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
        return marker, True
    except FileExistsError:
        existing = _read_json(marker)
        if existing.get("source_sha256") != source_sha256:
            raise RuntimeError(f"consumed run hash drift: {run_id}")
        return marker, False


def _finish_source(marker: Path, reconciliation: str, receipt_path: Path) -> None:
    record = _read_json(marker)
    record.update({
        "status": "CONSUMED" if reconciliation == "PASS" else "ATTENTION_REQUIRED",
        "reconciliation": reconciliation,
        "receipt_path": str(receipt_path),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, marker)


def _reconcile_existing_orders(market: str, adapter: FutuAdapter) -> list[dict[str, Any]]:
    journal = _journal_factory(market)
    token = journal.acquire_lease()
    if not token:
        journal.close()
        raise RuntimeError(f"{market} reconciliation lease is already ACTIVE")
    try:
        journal.reconcile(adapter)
        return journal.unresolved_orders()
    finally:
        journal.release_lease()
        journal.close()


def run(
    market: str,
    execute_sim: bool = False,
    *,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    market = market.upper()
    if market not in {"HK", "US"}:
        raise ValueError("market must be HK or US")

    source_path, payload, manifest = select_latest_artifact(market)
    selected_run_id = str(manifest.get("run_id") or "")
    if expected_run_id is not None and selected_run_id != expected_run_id:
        raise RuntimeError(
            f"source run mismatch: expected={expected_run_id}, selected={selected_run_id}"
        )
    adapter = FutuAdapter()
    connected, connection_message = adapter.test_connection(timeout=1.5)
    if not connected:
        raise RuntimeError(connection_message)

    account_q = adapter.get_account_info(market)
    positions_q = adapter.get_positions(market)
    account = account_q.require(f"{market} SIMULATE account")
    positions_before = positions_q.require(f"{market} SIMULATE positions")
    symbols = [str(i["symbol"]) for i in payload.get("paper_intents") or []]
    quotes = adapter.fetch_quotes(symbols)
    if set(symbols) - set(quotes):
        raise RuntimeError(f"missing quotes: {sorted(set(symbols) - set(quotes))}")

    orders, skipped = build_orders(payload, account, positions_before, quotes)
    results: list[dict[str, Any]] = []
    executor_error = ""
    source_sha256 = _sha256(source_path)
    run_id = selected_run_id
    marker: Path | None = None
    source_reserved = False
    unresolved_orders: list[dict[str, Any]] = []
    if execute_sim:
        if not run_id:
            raise RuntimeError("source manifest has no run_id")
        # Reconcile every historical intent before reserving a new source run.
        # A different Northstar run id can express the same position change, so
        # source-level idempotency alone is insufficient while an older broker
        # order remains non-terminal.
        try:
            unresolved_orders = _reconcile_existing_orders(market, adapter)
        except Exception as exc:
            executor_error = f"PRE_SUBMIT_RECONCILIATION_FAILED: {exc}"
        if unresolved_orders:
            executor_error = "UNRESOLVED_PRIOR_ORDERS_BLOCK_NEW_SUBMISSION"
            skipped.append({
                "symbol": "*", "action": "NONE", "reason": executor_error,
            })
        elif not executor_error:
            marker, source_reserved = _reserve_source(market, run_id, source_sha256)
            if not source_reserved:
                skipped.append({
                    "symbol": "*", "action": "NONE", "reason": "SOURCE_RUN_ALREADY_CONSUMED",
                })

    if execute_sim and orders and source_reserved:
        executor = OrderExecutor(
            dry_run=False,
            journal_factory=_journal_factory,
            terminal_poll_timeout_seconds=20,
            terminal_poll_interval_seconds=5,
        )
        try:
            results = executor.execute_orders(orders)
        except Exception as exc:
            # A broker submit can succeed before reconciliation fails.  Preserve
            # that ambiguity in a durable receipt and never retry blindly.
            executor_error = str(exc)
    elif orders and not execute_sim:
        executor = OrderExecutor(dry_run=True)
        results = executor.execute_orders(orders)

    positions_after_q = adapter.get_positions(market)
    positions_after = positions_after_q.data if positions_after_q.ok else None
    statuses = [str(r.get("status") or "") for r in results]
    uncertain = sorted(set(statuses) & OrderStatus.uncertain_set())
    reconciliation = (
        "ATTENTION_REQUIRED"
        if executor_error or uncertain or unresolved_orders or positions_after is None
        else "PASS"
    )
    receipt = {
        "schema_version": "1.0",
        "mode": "FUTU_SIM_FORWARD",
        "real_trading_allowed": False,
        "research_status": payload.get("validation_status"),
        "formal_publish_allowed": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "market": market,
        "execution_requested": execute_sim,
        "source_reserved_this_run": source_reserved,
        "source": {
            "path": str(source_path),
            "sha256": source_sha256,
            "manifest_run_id": manifest.get("run_id"),
            "signal_asof": payload.get("signal_asof"),
        },
        "connection": connection_message,
        "account_before": account,
        "positions_before": positions_before,
        "quotes": quotes,
        "planned_orders": orders,
        "skipped_intents": skipped,
        "results": results,
        "executor_error": executor_error or None,
        "unresolved_orders": unresolved_orders,
        "positions_after": positions_after,
        "reconciliation": reconciliation,
        "uncertain_statuses": uncertain,
    }
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    suffix = "execute" if execute_sim else "preview"
    receipt_path = RECEIPTS / f"{stamp}_{market.lower()}_{suffix}.json"
    tmp = receipt_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, receipt_path)
    if marker is not None:
        _finish_source(marker, receipt["reconciliation"], receipt_path)
    receipt["receipt_path"] = str(receipt_path)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True, choices=("HK", "US", "hk", "us"))
    parser.add_argument("--execute-sim", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = run(args.market, execute_sim=args.execute_sim)
        print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))
        return 2 if receipt["reconciliation"] != "PASS" else 0
    except Exception as exc:
        print(json.dumps({
            "mode": "FUTU_SIM_FORWARD",
            "market": args.market.upper(),
            "status": "FAILED_CLOSED",
            "error": str(exc),
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
