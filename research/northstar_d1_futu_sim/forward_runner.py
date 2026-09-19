"""Generate a completed-daily Northstar signal and execute it in Futu SIMULATE.

One invocation owns the complete forward path for one market.  The research
model stays account-blind; only the existing sibling execution harness receives
the immutable, hash-verified run id after every research gate passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from research.northstar_d1.runner import OUTPUT_ROOT, run_market, write_report
from research.northstar_d1.runtime import RunCoordinator

from .report import build_report
from .runner import run as execute_forward


ROOT = Path(__file__).resolve().parent
FORWARD_RECEIPTS = ROOT / "forward_receipts"
FORWARD_RUNTIME = ROOT / "runtime"
WINDOW_START = time(9, 35)
WINDOW_END = time(10, 0)
FORWARD_DEPLOYMENTS = {
    "HK": {
        "deployment_id": "NORTHSTAR_D1_HK",
        "deployment_name": "Northstar-D1 HK Forward",
        "timezone": "Asia/Hong_Kong",
        "schedule_timezone": "Asia/Hong_Kong",
        "schedule_hour": 9,
        "schedule_minute": 35,
        "schedule_weekdays": (0, 1, 2, 3, 4),
        "task_name": r"\NorthstarD1-Forward-HK",
    },
    "US": {
        "deployment_id": "NORTHSTAR_D1_US",
        "deployment_name": "Northstar-D1 US Forward",
        "timezone": "America/New_York",
        "schedule_timezone": "America/New_York",
        "schedule_hour": 9,
        "schedule_minute": 35,
        "schedule_weekdays": (0, 1, 2, 3, 4),
        "task_name": r"\NorthstarD1-Forward-US",
    },
}


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def execution_window(market: str, observed_at: datetime | None = None) -> dict[str, Any]:
    market = market.upper()
    deployment = FORWARD_DEPLOYMENTS[market]
    local = _utc(observed_at).astimezone(ZoneInfo(deployment["schedule_timezone"]))
    if local.weekday() not in deployment["schedule_weekdays"]:
        status = "SKIPPED_NON_WEEKDAY"
    elif local.time().replace(tzinfo=None) < WINDOW_START:
        status = "SKIPPED_EARLY"
    elif local.time().replace(tzinfo=None) > WINDOW_END:
        status = "MISSED_EXECUTION_WINDOW"
    else:
        status = "READY"
    return {
        "status": status,
        "market": market,
        "market_timezone": deployment["schedule_timezone"],
        "observed_at_local": local.isoformat(),
        "window": "09:35-10:00",
    }


def scheduler_contract(market: str) -> dict[str, Any]:
    market = market.upper()
    deployment = FORWARD_DEPLOYMENTS[market]
    triggers = ["09:35 Asia/Hong_Kong"] if market == "HK" else [
        "21:35 Asia/Shanghai",
        "22:35 Asia/Shanghai",
    ]
    return {
        "market": market,
        "task_name": deployment["task_name"],
        "market_local_window": "09:35-10:00",
        "triggers": triggers,
        "dst_policy": "DUAL_SHANGHAI_TRIGGER_WITH_NEW_YORK_GATE" if market == "US" else "FIXED_HK_LOCAL",
        "start_when_available": True,
        "multiple_instances": "IGNORE_NEW",
    }


def scheduler_contract_sha256(market: str) -> str:
    raw = json.dumps(
        scheduler_contract(market), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()


def _write_forward_receipt(payload: dict[str, Any]) -> Path:
    FORWARD_RECEIPTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    market = str(payload.get("market") or "unknown").lower()
    path = FORWARD_RECEIPTS / f"{stamp}_{market}_forward.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return path


def _replace_forward_receipt(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def _scheduler_environment(market: str, window: dict[str, Any]) -> Iterator[None]:
    deployment = FORWARD_DEPLOYMENTS[market]
    values = {
        "NORTHSTAR_INVOCATION_SOURCE": "WINDOWS_TASK_SCHEDULER",
        "NORTHSTAR_INVOCATION_RUN_KIND": "SCHEDULED",
        "NORTHSTAR_SCHEDULER_TASK_NAME": deployment["task_name"],
        "NORTHSTAR_SCHEDULER_CONTRACT_SHA256": scheduler_contract_sha256(market),
    }
    prior = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _manual_validation_environment(market: str) -> Iterator[None]:
    values = {
        "NORTHSTAR_INVOCATION_SOURCE": "USER_AUTHORIZED_MANUAL_VALIDATION",
        "NORTHSTAR_INVOCATION_RUN_KIND": "MANUAL_VALIDATION",
        "NORTHSTAR_SCHEDULER_TASK_NAME": "",
        "NORTHSTAR_SCHEDULER_CONTRACT_SHA256": "",
    }
    prior = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _require_live_market_snapshots(payload: dict[str, Any]) -> None:
    signals = payload.get("signals") or []
    if not signals:
        raise RuntimeError("no signals available for live-session execution")
    closed = []
    for signal in signals:
        snapshot = ((signal.get("market_data") or {}).get("snapshot") or {})
        if snapshot.get("market_live") is not True:
            closed.append(
                {
                    "symbol": signal.get("symbol"),
                    "market_state": snapshot.get("market_state"),
                }
            )
    if closed:
        raise RuntimeError(f"market session is not live for all symbols: {closed}")


def run_integrated(
    market: str,
    *,
    observed_at: datetime | None = None,
    output_root: Path = OUTPUT_ROOT,
    data_source=None,
    model=None,
    execute_sim: bool = True,
    manual_validation: bool = False,
) -> dict[str, Any]:
    market = market.upper()
    if market not in FORWARD_DEPLOYMENTS:
        raise ValueError("market must be HK or US")
    now_utc = _utc(observed_at)
    window = execution_window(market, now_utc)
    if manual_validation:
        window = {
            **window,
            "natural_window_status": window["status"],
            "status": "READY_MANUAL_VALIDATION",
            "manual_validation": True,
            "authorization_scope": "FUTU_SIMULATE_ONLY",
        }
    elif window["status"] != "READY":
        result = {
            "schema_version": "1.0",
            "mode": "NORTHSTAR_D1_INTEGRATED_FORWARD",
            "market": market,
            "status": window["status"],
            "execution_requested": False,
            "window": window,
        }
        result["receipt_path"] = str(_write_forward_receipt(result))
        return result

    deployment = FORWARD_DEPLOYMENTS[market]
    coordinator_deployment = deployment
    if manual_validation:
        manual_local = now_utc.astimezone(ZoneInfo(deployment["schedule_timezone"]))
        coordinator_deployment = {
            **deployment,
            "deployment_name": f"{deployment['deployment_name']} Manual Validation",
            "schedule_hour": manual_local.hour,
            "schedule_minute": manual_local.minute,
            "schedule_weekdays": tuple(range(7)),
        }
    paths: dict[str, Any] | None = None
    invocation_environment = (
        _manual_validation_environment(market)
        if manual_validation
        else _scheduler_environment(market, window)
    )
    with invocation_environment:
        coordinator = RunCoordinator(
            market=market,
            deployment=coordinator_deployment,
            output_root=output_root,
            now=now_utc,
        )
        decision = coordinator.acquire()
        if decision.action == "DUPLICATE_SUPPRESSED":
            result = {
                "schema_version": "1.0",
                "mode": "NORTHSTAR_D1_INTEGRATED_FORWARD",
                "market": market,
                "status": decision.action,
                "run_id": decision.run_id,
                "execution_requested": False,
                "window": window,
            }
            result["receipt_path"] = str(_write_forward_receipt(result))
            return result
        if not decision.should_run:
            raise RuntimeError(f"forward slot blocked: {decision.action}: {decision.reason}")

        try:
            # CompletedDailyDataSource forbids injected clocks in live scans.
            # Keep observed_at for the scheduler/window contract, but let the
            # production data pipeline obtain its own authoritative clock.
            model_now = now_utc if data_source is not None else None
            payload = run_market(
                market,
                now=model_now,
                data_source=data_source,
                model=model,
            )
            payload["run"] = coordinator.metadata()
            payload["report_generated_at_utc"] = datetime.now(timezone.utc).isoformat()
            paths = write_report(payload, output_root, run_id=decision.run_id)
            if payload.get("run_verdict") != "RUN_PASS":
                coordinator.mark_failed(
                    "research run gate checks did not all pass",
                    exit_code=1,
                    paths=paths,
                )
                raise RuntimeError("research run failed closed")
            _require_live_market_snapshots(payload)

            execution = execute_forward(
                market,
                execute_sim=execute_sim,
                expected_run_id=decision.run_id,
            )
            # A source run is single-use once handed to the broker boundary.  Mark
            # the research slot complete even when reconciliation needs attention;
            # this prevents a new run id from causing a blind resubmission.
            coordinator.mark_success(paths)
            result = {
                "schema_version": "1.0",
                "mode": "NORTHSTAR_D1_INTEGRATED_FORWARD",
                "market": market,
                "status": "PASS" if execution.get("reconciliation") == "PASS" else "ATTENTION_REQUIRED",
                "run_id": decision.run_id,
                "signal_asof": payload.get("signal_asof"),
                "research_artifacts": paths,
                "execution_receipt": execution.get("receipt_path"),
                "reconciliation": execution.get("reconciliation"),
                "report": None,
                "report_error": None,
                "window": window,
            }
            # The report resolver is anchored to the current Forward receipt, so
            # publish that receipt before rendering and then atomically enrich it
            # with the resulting report metadata.
            receipt_path = _write_forward_receipt(result)
            result["receipt_path"] = str(receipt_path)
            try:
                result["report"] = build_report(market=market)
            except Exception as exc:
                result["report_error"] = str(exc)
            _replace_forward_receipt(receipt_path, result)
            return result
        except Exception as exc:
            # If mark_success already ran, mark_failed will reject lock ownership;
            # this branch is reached only before successful finalization.
            try:
                coordinator.mark_failed(str(exc), exit_code=1, paths=paths)
            except Exception:
                pass
            result = {
                "schema_version": "1.0",
                "mode": "NORTHSTAR_D1_INTEGRATED_FORWARD",
                "market": market,
                "status": "FAILED_CLOSED",
                "run_id": decision.run_id,
                "signal_asof": (payload or {}).get("signal_asof") if "payload" in locals() else None,
                "research_artifacts": paths,
                "execution_receipt": None,
                "reconciliation": "NOT_EXECUTED",
                "error": str(exc),
                "window": window,
            }
            result["receipt_path"] = str(_write_forward_receipt(result))
            return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True, choices=("HK", "US", "hk", "us"))
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--manual-validation", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_integrated(
            args.market,
            execute_sim=not args.preview,
            manual_validation=args.manual_validation,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if result["status"] in {"PASS", "DUPLICATE_SUPPRESSED", "SKIPPED_EARLY", "SKIPPED_NON_WEEKDAY"}:
            return 0
        return 2
    except Exception as exc:
        failure = {
            "mode": "NORTHSTAR_D1_INTEGRATED_FORWARD",
            "market": args.market.upper(),
            "status": "FAILED_CLOSED",
            "error": str(exc),
        }
        failure["receipt_path"] = str(_write_forward_receipt(failure))
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
