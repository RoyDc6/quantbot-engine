import json
from datetime import datetime, timezone

import research.northstar_d1_futu_sim.workbuddy_entry as module


def test_current_forward_is_reused_without_manual_execution(monkeypatch):
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
    monkeypatch.setattr(
        module,
        "build_report",
        lambda **kwargs: {"status": "REPORT_READY", "markets": ["HK"]},
    )

    result = module.run(
        "HK", now=datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)
    )

    assert result["trigger_mode"] == "CURRENT_FORWARD_REUSED"
    assert result["prior_forward_status"] == "PASS"
    assert result["manual_validation"] is None
    assert observed["manual_calls"] == 0


def test_missing_forward_runs_manual_validation_before_report(monkeypatch):
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
        return {"status": "REPORT_READY", "markets": ["HK"]}

    monkeypatch.setattr(module, "_latest_forward_receipt", missing)
    monkeypatch.setattr(module, "run_integrated", manual)
    monkeypatch.setattr(module, "build_report", report)

    result = module.run(
        "HK", now=datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)
    )

    assert events == [("manual", "HK", True), ("report", "HK")]
    assert result["trigger_mode"] == "MANUAL_VALIDATION_RECOVERY"
    assert result["prior_forward_status"] == "MISSING"
    assert result["manual_validation"]["status"] == "PASS"


def test_scheduled_delivery_never_starts_manual_execution(monkeypatch):
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
        lambda **kwargs: {"status": "REPORT_READY", "markets": ["US"]},
    )

    result = module.run(
        "US",
        now=datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc),
        scheduled_delivery=True,
    )

    assert result["trigger_mode"] == "SCHEDULED_DELIVERY"
    assert result["prior_forward_status"] == "FAILED_CLOSED"
    assert result["manual_validation"] is None
    assert events == []


def test_delivery_refreshes_existing_execution_without_running_strategy(monkeypatch):
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
        lambda path, payload: events.append((path, payload)),
    )
    monkeypatch.setattr(
        module,
        "build_report",
        lambda **kwargs: {"status": "REPORT_READY", "markets": ["HK"]},
    )

    result = module.run(
        "HK",
        now=datetime(2026, 9, 23, 1, 50, tzinfo=timezone.utc),
        scheduled_delivery=True,
    )

    assert "strategy" not in events
    updated = next(item[1] for item in events if isinstance(item, tuple))
    assert updated["execution_receipt"] == "reconcile.json"
    assert updated["status"] == "PASS"
    assert result["delivery_reconciliation"]["mode"] == "RECONCILIATION_ONLY_NO_BROKER_MUTATION"


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
