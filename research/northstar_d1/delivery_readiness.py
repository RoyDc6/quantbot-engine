"""Read-only delivery gate for Windows-scheduled Northstar-D1 reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .runner import OUTPUT_ROOT, deployment_for
from .windows_scheduler_entry import WINDOWS_TASK_CONTRACTS, latest_due_slot


REQUIRED_MARKDOWN_SECTIONS = (
    "## 汇总",
    "## 信号",
    "## 三层决策审计（私有）",
    "### 结构信号",
    "### 序列信号",
    "## 数据新鲜度证据",
    "## Paper intents（非订单）",
    "## 综述结论",
)
REQUIRED_COMPANY_NAME_HEADERS = (
    "| 标的代码 | 公司名称 | 日线收盘 |",
    "| 标的代码 | 公司名称 | 候选→输出 |",
    "| 标的代码 | 公司名称 | 计数 |",
    "| 标的代码 | 公司名称 | 来源 |",
)
ALLOWED_RUN_KINDS = frozenset({"SCHEDULED", "MISSED_RECOVERY"})


class DeliveryNotReady(RuntimeError):
    """The expected report is absent, stale, or failed a delivery gate."""


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        raise DeliveryNotReady(f"JSON_NOT_READABLE:{path}") from exc
    if not isinstance(value, dict):
        raise DeliveryNotReady(f"JSON_NOT_OBJECT:{path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise DeliveryNotReady(f"FILE_NOT_READABLE:{path}") from exc
    return digest.hexdigest().upper()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise DeliveryNotReady(reason)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _validate_markdown(
    markdown: str, signals: List[Dict[str, Any]], paper_intents: List[Dict[str, Any]]
) -> None:
    for section in REQUIRED_MARKDOWN_SECTIONS:
        _require(section in markdown, f"MARKDOWN_SECTION_MISSING:{section}")
    for header in REQUIRED_COMPANY_NAME_HEADERS:
        _require(header in markdown, f"COMPANY_NAME_HEADER_MISSING:{header}")
    for item in signals:
        pair = f"| {item['symbol']} | {item['name']} |"
        _require(
            markdown.count(pair) >= 4,
            f"COMPANY_NAME_TABLE_COVERAGE_INCOMPLETE:{item['symbol']}",
        )
    name_by_symbol = {str(item["symbol"]): str(item["name"]) for item in signals}
    for intent in paper_intents:
        symbol = str(intent.get("symbol") or "")
        name = str(intent.get("name") or name_by_symbol.get(symbol) or "")
        _require(
            f"- {symbol} {name} ·" in markdown,
            f"PAPER_INTENT_COMPANY_NAME_MISSING:{symbol}",
        )


def inspect_delivery(
    market: str,
    *,
    output_root: Path = OUTPUT_ROOT,
    observed_at: datetime | None = None,
) -> Dict[str, Any]:
    """Verify the expected immutable Markdown and return its delivery receipt."""

    market = market.upper()
    deployment = deployment_for(market)
    expected_slot = latest_due_slot(market, observed_at)
    expected_slot_key = (
        f"{market}:{expected_slot.date().isoformat()}:"
        f"{int(deployment['schedule_hour']):02d}:"
        f"{int(deployment['schedule_minute']):02d}:"
        f"{deployment['schedule_timezone']}"
    )
    market_root = Path(output_root) / market.lower()
    state = _read_json(market_root / ".runtime" / "latest.json")
    _require(state.get("status") == "SUCCESS", "LATEST_STATUS_NOT_SUCCESS")
    _require(state.get("slot_key") == expected_slot_key, "EXPECTED_SLOT_NOT_READY")
    _require(
        state.get("invocation_source") == "WINDOWS_TASK_SCHEDULER",
        "SOURCE_NOT_WINDOWS_TASK_SCHEDULER",
    )
    _require(
        state.get("invocation_run_kind") in ALLOWED_RUN_KINDS,
        "RUN_KIND_NOT_DELIVERABLE",
    )
    _require(
        state.get("scheduler_task_name")
        == WINDOWS_TASK_CONTRACTS[market]["task_name"],
        "SCHEDULER_TASK_MISMATCH",
    )
    run_id = str(state.get("run_id") or "")
    _require(bool(run_id), "RUN_ID_MISSING")

    artifacts = state.get("artifacts")
    hashes = state.get("artifact_hashes")
    _require(isinstance(artifacts, dict), "ARTIFACTS_MISSING")
    _require(isinstance(hashes, dict), "ARTIFACT_HASHES_MISSING")
    archive_root = market_root / "runs" / run_id
    report_path = Path(str(artifacts.get("archive_markdown") or ""))
    json_path = Path(str(artifacts.get("archive_json") or ""))
    manifest_path = Path(str(artifacts.get("archive_manifest") or ""))
    for label, path in (
        ("MARKDOWN", report_path),
        ("JSON", json_path),
        ("MANIFEST", manifest_path),
    ):
        _require(_inside(path, archive_root), f"{label}_PATH_OUTSIDE_ARCHIVE")
        _require(path.is_file(), f"{label}_MISSING")

    report_hash = _sha256(report_path)
    json_hash = _sha256(json_path)
    _require(
        report_hash == str(hashes.get("archive_markdown") or "").upper(),
        "MARKDOWN_STATE_HASH_MISMATCH",
    )
    _require(
        json_hash == str(hashes.get("archive_json") or "").upper(),
        "JSON_STATE_HASH_MISMATCH",
    )

    manifest = _read_json(manifest_path)
    _require(manifest.get("run_id") == run_id, "MANIFEST_RUN_ID_MISMATCH")
    _require(manifest.get("market") == market, "MANIFEST_MARKET_MISMATCH")
    _require(manifest.get("run_verdict") == "RUN_PASS", "MANIFEST_NOT_RUN_PASS")
    _require(
        Path(str((manifest.get("archive") or {}).get("markdown") or "")).resolve()
        == report_path.resolve(),
        "MANIFEST_MARKDOWN_PATH_MISMATCH",
    )
    _require(
        str((manifest.get("sha256") or {}).get("markdown") or "").upper()
        == report_hash,
        "MARKDOWN_MANIFEST_HASH_MISMATCH",
    )
    _require(
        str((manifest.get("sha256") or {}).get("json") or "").upper()
        == json_hash,
        "JSON_MANIFEST_HASH_MISMATCH",
    )

    payload = _read_json(json_path)
    execution = payload.get("execution") or {}
    summary = payload.get("summary") or {}
    _require(payload.get("deployment_id") == f"NORTHSTAR_D1_{market}", "DEPLOYMENT_MISMATCH")
    _require(payload.get("market") == market, "PAYLOAD_MARKET_MISMATCH")
    _require(payload.get("run_verdict") == "RUN_PASS", "PAYLOAD_NOT_RUN_PASS")
    _require(payload.get("mode") == "RESEARCH_ONLY", "MODE_NOT_RESEARCH_ONLY")
    _require(payload.get("validation_status") == "NOT_EVALUATED", "VALIDATION_STATUS_DRIFT")
    _require(payload.get("formal_publish_allowed") is False, "FORMAL_PUBLISH_DRIFT")
    _require(payload.get("data_ready") is True, "DATA_NOT_READY")
    _require(payload.get("freshness_ready") is True, "FRESHNESS_NOT_READY")
    _require(payload.get("report_quality_ready") is True, "REPORT_QUALITY_NOT_READY")
    _require(execution.get("enabled") is False, "EXECUTION_ENABLED")
    _require(execution.get("account_access") is False, "ACCOUNT_ACCESS_DETECTED")
    _require(execution.get("order_api_called") is False, "ORDER_API_CALLED")
    _require(payload.get("orders") == [], "ORDERS_NOT_EMPTY")
    _require(payload.get("fills") == [], "FILLS_NOT_EMPTY")
    _require(summary.get("errors") == 0 and payload.get("errors") == [], "REPORT_ERRORS_PRESENT")
    signals = payload.get("signals")
    _require(isinstance(signals, list) and bool(signals), "SIGNALS_MISSING")
    for item in signals:
        _require(isinstance(item, dict), "SIGNAL_NOT_OBJECT")
        symbol = str(item.get("symbol") or "").strip()
        name = str(item.get("name") or "").strip()
        _require(bool(symbol), "SIGNAL_SYMBOL_MISSING")
        _require(bool(name) and name != symbol, f"COMPANY_NAME_MISSING:{symbol}")

    try:
        markdown = report_path.read_text(encoding="utf-8")
        _validate_markdown(markdown, signals, payload.get("paper_intents") or [])
    except (OSError, DeliveryNotReady) as original_error:
        correction_root = market_root / "delivery_corrections" / run_id
        corrected_path = correction_root / f"{report_path.stem}.corrected.md"
        corrected_manifest_path = correction_root / f"{report_path.stem}.corrected.manifest.json"
        _require(corrected_path.is_file(), f"DELIVERY_CORRECTION_MISSING:{original_error}")
        _require(corrected_manifest_path.is_file(), "DELIVERY_CORRECTION_MANIFEST_MISSING")
        correction_manifest = _read_json(corrected_manifest_path)
        _require(
            correction_manifest.get("source_run_id") == run_id,
            "DELIVERY_CORRECTION_RUN_ID_MISMATCH",
        )
        _require(
            str(correction_manifest.get("source_json_sha256") or "").upper()
            == json_hash,
            "DELIVERY_CORRECTION_SOURCE_JSON_MISMATCH",
        )
        _require(
            str(correction_manifest.get("source_markdown_sha256") or "").upper()
            == report_hash,
            "DELIVERY_CORRECTION_SOURCE_MARKDOWN_MISMATCH",
        )
        corrected_hash = _sha256(corrected_path)
        _require(
            str(correction_manifest.get("corrected_markdown_sha256") or "").upper()
            == corrected_hash,
            "DELIVERY_CORRECTION_HASH_MISMATCH",
        )
        try:
            markdown = corrected_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise DeliveryNotReady("DELIVERY_CORRECTION_NOT_READABLE") from exc
        _validate_markdown(markdown, signals, payload.get("paper_intents") or [])
        report_path = corrected_path
        report_hash = corrected_hash

    return {
        "status": "DELIVERY_READY",
        "market": market,
        "deployment_id": payload["deployment_id"],
        "run_id": run_id,
        "slot_key": expected_slot_key,
        "run_kind": state["invocation_run_kind"],
        "signal_asof": payload.get("signal_asof"),
        "report_path": str(report_path),
        "report_sha256": report_hash,
        "summary": {
            "buy": summary.get("buy"),
            "sell": summary.get("sell"),
            "hold": summary.get("hold"),
            "errors": summary.get("errors"),
        },
        "present_files_count": 1,
        "present_only_this_markdown": True,
        "boundaries": [
            "RESEARCH_ONLY",
            "NOT_EVALUATED",
            "formal_publish_allowed=false",
            "ZERO_EXECUTION",
        ],
    }


def wait_for_delivery(
    market: str,
    *,
    output_root: Path = OUTPUT_ROOT,
    wait_seconds: int = 0,
    poll_seconds: int = 15,
) -> Dict[str, Any]:
    deadline = time.monotonic() + max(0, wait_seconds)
    last_reason = "REPORT_NOT_CHECKED"
    while True:
        try:
            return inspect_delivery(market, output_root=output_root)
        except DeliveryNotReady as exc:
            last_reason = str(exc)
        if time.monotonic() >= deadline:
            raise DeliveryNotReady(last_reason)
        time.sleep(max(1, min(poll_seconds, int(deadline - time.monotonic()) or 1)))


def main_for_market(market: str, argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Northstar-D1 {market} delivery-only gate")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--poll-seconds", type=int, default=15)
    args = parser.parse_args(argv)
    try:
        result = wait_for_delivery(
            market,
            output_root=args.output_root,
            wait_seconds=args.wait_seconds,
            poll_seconds=args.poll_seconds,
        )
    except DeliveryNotReady as exc:
        print(
            json.dumps(
                {
                    "status": "DELIVERY_NOT_READY",
                    "market": market.upper(),
                    "reason": str(exc),
                    "futu_called": False,
                    "model_called": False,
                    "account_access": False,
                    "order_api_called": False,
                },
                ensure_ascii=False,
            )
        )
        return 4
    print(json.dumps(result, ensure_ascii=False))
    return 0
