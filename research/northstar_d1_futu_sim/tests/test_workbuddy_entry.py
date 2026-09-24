import copy
import json
from pathlib import Path
from datetime import datetime, timezone

import research.northstar_d1_futu_sim.workbuddy_entry as module


def _stub_report(monkeypatch, tmp_path, market):
    reports = tmp_path / "reports"
    reports.mkdir()
    path = reports / f"{market.lower()}.md"
    path.write_text("report", encoding="utf-8")
    monkeypatch.setattr(module, "REPORTS", reports)
    return {
        "status": "REPORT_READY", "markets": [market],
        "report_path": str(path), "sha256": module._sha256(path),
    }


def test_current_forward_is_reused_without_manual_execution(monkeypatch, tmp_path):
    observed = {"manual_calls": 0}

    monkeypatch.setattr(
        module,
        "_latest_forward_receipt",
        lambda *args, **kwargs: ("forward.json", {"status": "PASS"}),
    )

    def unexpected_manual(*args, **kwargs):
        observed["manual_calls"] += 1
        raise AssertionError("current PASS must not execute again")

    monkeypatch.setattr(module, "run_integrated", unexpected_manual)
    report = _stub_report(monkeypatch, tmp_path, "HK")
    monkeypatch.setattr(
        module,
        "build_report",
        lambda **kwargs: report,
    )
    monkeypatch.setattr(module, "_replace_forward_receipt", lambda *args: None)

    result = module.run(
        "HK", now=datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)
    )

    assert result["trigger_mode"] == "CURRENT_FORWARD_REUSED"
    assert result["prior_forward_status"] == "PASS"
    assert result["manual_validation"] is None
    assert observed["manual_calls"] == 0


def test_missing_forward_runs_manual_validation_before_report(monkeypatch, tmp_path):
    events = []
    lookups = {"count": 0}

    def missing(*args, **kwargs):
        lookups["count"] += 1
        if lookups["count"] == 1:
            raise RuntimeError("no current integrated Forward receipt")
        return ("forward.json", {"status": "PASS"})

    def manual(market, *, manual_validation):
        events.append(("manual", market, manual_validation))
        return {"status": "PASS", "run_id": "RUN-HK"}

    def report(**kwargs):
        events.append(("report", kwargs["market"]))
        return report_metadata

    report_metadata = _stub_report(monkeypatch, tmp_path, "HK")

    monkeypatch.setattr(module, "_latest_forward_receipt", missing)
    monkeypatch.setattr(module, "run_integrated", manual)
    monkeypatch.setattr(module, "build_report", report)
    monkeypatch.setattr(module, "_replace_forward_receipt", lambda *args: None)

    result = module.run(
        "HK", now=datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)
    )

    assert events == [("manual", "HK", True), ("report", "HK")]
    assert result["trigger_mode"] == "MANUAL_VALIDATION_RECOVERY"
    assert result["prior_forward_status"] == "MISSING"
    assert result["manual_validation"]["status"] == "PASS"


def test_scheduled_delivery_never_starts_manual_execution(monkeypatch, tmp_path):
    events = []

    monkeypatch.setattr(
        module,
        "_latest_forward_receipt",
        lambda *args, **kwargs: ("forward.json", {"status": "FAILED_CLOSED"}),
    )
    monkeypatch.setattr(
        module,
        "run_integrated",
        lambda *args, **kwargs: events.append("manual"),
    )
    monkeypatch.setattr(
        module,
        "build_report",
        lambda **kwargs: report,
    )
    report = _stub_report(monkeypatch, tmp_path, "US")
    monkeypatch.setattr(module, "_replace_forward_receipt", lambda *args: None)

    result = module.run(
        "US",
        now=datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc),
        scheduled_delivery=True,
    )

    assert result["trigger_mode"] == "SCHEDULED_DELIVERY"
    assert result["prior_forward_status"] == "FAILED_CLOSED"
    assert result["manual_validation"] is None
    assert events == []


def test_delivery_refreshes_existing_execution_without_running_strategy(monkeypatch, tmp_path):
    events = []
    forward = {
        "status": "ATTENTION_REQUIRED",
        "execution_receipt": "execute.json",
    }

    monkeypatch.setattr(
        module,
        "_latest_forward_receipt",
        lambda *args, **kwargs: ("forward.json", dict(forward)),
    )
    monkeypatch.setattr(
        module,
        "run_integrated",
        lambda *args, **kwargs: events.append("strategy"),
    )
    monkeypatch.setattr(
        module,
        "refresh_execution_receipt",
        lambda *args, **kwargs: {
            "receipt_path": "reconcile.json",
            "source_execution_receipt": "execute.json",
            "order_reconciliation": "PASS",
            "account_risk_flags": [],
            "reconciliation": "PASS",
            "reconciliation_refreshed_at_utc": "2026-09-23T01:49:00+00:00",
        },
    )
    monkeypatch.setattr(
        module,
        "_replace_forward_receipt",
        lambda path, payload: events.append((path, copy.deepcopy(payload))),
    )
    monkeypatch.setattr(
        module,
        "build_report",
        lambda **kwargs: report,
    )
    report = _stub_report(monkeypatch, tmp_path, "HK")

    result = module.run(
        "HK",
        now=datetime(2026, 9, 23, 1, 50, tzinfo=timezone.utc),
        scheduled_delivery=True,
    )

    assert "strategy" not in events
    updated = next(item[1] for item in events if isinstance(item, tuple))
    assert updated["execution_receipt"] == "reconcile.json"
    assert updated["status"] == "PASS"
    assert updated["report"] is None
    final = [item[1] for item in events if isinstance(item, tuple)][-1]
    assert final["report"] == report
    assert result["delivery_reconciliation"]["mode"] == "RECONCILIATION_ONLY_NO_BROKER_MUTATION"


def test_delivery_records_stale_old_hash_and_links_verified_new_report(monkeypatch, tmp_path):
    report = _stub_report(monkeypatch, tmp_path, "HK")
    forward_path = tmp_path / "forward.json"
    old_report = {**report, "sha256": "0" * 64}
    forward_path.write_text(json.dumps({
        "status": "PASS", "report": old_report,
    }), encoding="utf-8")
    monkeypatch.setattr(
        module, "_latest_forward_receipt",
        lambda *args, **kwargs: (
            forward_path, json.loads(forward_path.read_text(encoding="utf-8"))
        ),
    )
    monkeypatch.setattr(module, "build_report", lambda **kwargs: report)

    result = module.run("HK", now=datetime(2026, 9, 23, 1, 50, tzinfo=timezone.utc), scheduled_delivery=True)
    saved = json.loads(forward_path.read_text(encoding="utf-8"))

    assert saved["report"] == report
    assert result["sha256"] == report["sha256"]
    assert saved["report_history"][0]["sha256"] == "0" * 64
    assert saved["report_history"][0]["verified_at_rotation"] is False
    assert module._sha256(Path(saved["report"]["report_path"])) == saved["report"]["sha256"]


def test_main_keeps_stdout_as_single_json_when_dependency_prints(monkeypatch, capsys):
    def noisy_run(*args, **kwargs):
        print("sdk diagnostic")
        return {"status": "REPORT_READY", "markets": ["HK"]}

    monkeypatch.setattr(module, "run", noisy_run)

    assert module.main(["--market", "HK", "--scheduled-delivery"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "REPORT_READY", "markets": ["HK"]}
    assert captured.out.count("\n") == 1
    assert "sdk diagnostic" in captured.err
