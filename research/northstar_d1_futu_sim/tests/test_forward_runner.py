from datetime import datetime, timezone

import pytest

from research.northstar_d1_futu_sim.forward_runner import (
    _require_live_market_snapshots,
    execution_window,
    run_integrated,
    scheduler_contract,
)


def test_us_dst_trigger_maps_to_new_york_open_window():
    summer = execution_window(
        "US", datetime(2026, 7, 15, 13, 35, 30, tzinfo=timezone.utc)
    )
    winter = execution_window(
        "US", datetime(2026, 12, 15, 14, 35, 30, tzinfo=timezone.utc)
    )
    assert summer["status"] == "READY"
    assert summer["observed_at_local"].startswith("2026-07-15T09:35:30-04:00")
    assert winter["status"] == "READY"
    assert winter["observed_at_local"].startswith("2026-12-15T09:35:30-05:00")


def test_wrong_dst_trigger_and_late_recovery_fail_closed():
    winter_early = execution_window(
        "US", datetime(2026, 12, 15, 13, 35, tzinfo=timezone.utc)
    )
    summer_late = execution_window(
        "US", datetime(2026, 7, 15, 14, 35, tzinfo=timezone.utc)
    )
    assert winter_early["status"] == "SKIPPED_EARLY"
    assert summer_late["status"] == "MISSED_EXECUTION_WINDOW"


def test_manual_validation_is_explicit_and_can_enter_after_window(monkeypatch, tmp_path):
    import research.northstar_d1_futu_sim.forward_runner as module

    class Decision:
        action = "DUPLICATE_SUPPRESSED"
        should_run = False
        run_id = "NORTHSTAR_D1_HK-20260917T0935-A03"
        reason = None

    class Coordinator:
        def __init__(self, **kwargs):
            assert kwargs["now"] == datetime(2026, 9, 17, 2, 17, tzinfo=timezone.utc)
            assert kwargs["deployment"]["schedule_hour"] == 10
            assert kwargs["deployment"]["schedule_minute"] == 17
            assert kwargs["deployment"]["schedule_weekdays"] == tuple(range(7))

        def acquire(self):
            return Decision()

    monkeypatch.setattr(module, "RunCoordinator", Coordinator)
    monkeypatch.setattr(module, "FORWARD_RECEIPTS", tmp_path)
    result = run_integrated(
        "HK",
        observed_at=datetime(2026, 9, 17, 2, 17, tzinfo=timezone.utc),
        manual_validation=True,
    )
    assert result["status"] == "DUPLICATE_SUPPRESSED"
    assert result["window"]["status"] == "READY_MANUAL_VALIDATION"
    assert result["window"]["natural_window_status"] == "MISSED_EXECUTION_WINDOW"
    assert result["window"]["authorization_scope"] == "FUTU_SIMULATE_ONLY"


def test_us_contract_has_two_shanghai_triggers_and_new_york_gate():
    contract = scheduler_contract("US")
    assert contract["triggers"] == [
        "21:35 Asia/Shanghai",
        "22:35 Asia/Shanghai",
    ]
    assert contract["market_local_window"] == "09:35-10:00"


def test_execution_requires_every_signal_snapshot_to_be_live():
    live = {
        "signals": [
            {"symbol": "AMD.US", "market_data": {"snapshot": {"market_live": True}}}
        ]
    }
    _require_live_market_snapshots(live)

    closed = {
        "signals": [
            {
                "symbol": "AMD.US",
                "market_data": {
                    "snapshot": {"market_live": False, "market_state": "CLOSED"}
                },
            }
        ]
    }
    with pytest.raises(RuntimeError, match="market session is not live"):
        _require_live_market_snapshots(closed)


def test_integrated_path_passes_exact_generated_run_to_execution(monkeypatch, tmp_path):
    import research.northstar_d1_futu_sim.forward_runner as module

    observed = {}

    class Decision:
        action = "RUN"
        should_run = True
        run_id = "NORTHSTAR_D1_US-20260715T0935-A01"
        reason = None

    class Coordinator:
        def __init__(self, **kwargs):
            pass

        def acquire(self):
            return Decision()

        def metadata(self):
            return {"run_id": Decision.run_id}

        def mark_success(self, paths):
            observed["marked_success"] = paths

        def mark_failed(self, *args, **kwargs):
            observed["marked_failed"] = True

    payload = {
        "run_verdict": "RUN_PASS",
        "signal_asof": "2026-07-14",
        "signals": [{
            "symbol": "AMD.US",
            "market_data": {"snapshot": {"market_live": True}},
        }],
    }
    monkeypatch.setattr(module, "RunCoordinator", Coordinator)
    def fake_run_market(*args, **kwargs):
        observed["model_now"] = kwargs.get("now")
        return payload

    monkeypatch.setattr(module, "run_market", fake_run_market)
    monkeypatch.setattr(module, "write_report", lambda *args, **kwargs: {"json": "x"})

    def fake_execute(market, execute_sim, expected_run_id):
        observed.update({
            "market": market,
            "execute_sim": execute_sim,
            "expected_run_id": expected_run_id,
        })
        return {"reconciliation": "PASS", "receipt_path": "receipt.json"}

    monkeypatch.setattr(module, "execute_forward", fake_execute)
    monkeypatch.setattr(module, "FORWARD_RECEIPTS", tmp_path)

    def fake_build_report(**kwargs):
        receipts = list(tmp_path.glob("*_us_forward.json"))
        assert len(receipts) == 1
        import json
        current = json.loads(receipts[0].read_text(encoding="utf-8"))
        assert current["run_id"] == Decision.run_id
        assert current["status"] == "PASS"
        return {"status": "REPORT_READY"}

    monkeypatch.setattr(module, "build_report", fake_build_report)

    result = run_integrated(
        "US",
        observed_at=datetime(2026, 7, 15, 13, 35, 30, tzinfo=timezone.utc),
    )

    assert result["status"] == "PASS"
    assert observed["expected_run_id"] == Decision.run_id
    assert observed["execute_sim"] is True
    assert observed["model_now"] is None
    assert "marked_failed" not in observed
