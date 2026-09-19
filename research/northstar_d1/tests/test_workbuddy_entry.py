from __future__ import annotations

import os

from research.northstar_d1 import workbuddy_entry


HK_AUTOMATION_ID = "automation-1785736457372"


def _verified(*, run_kind: str = "scheduled", market: str = "HK"):
    return {
        "provenance_status": "VERIFIED",
        "reason": "ACTIVE_WORKBUDDY_RUN_RECORD_MATCHED",
        "automation_id": HK_AUTOMATION_ID,
        "automation_run_id": "run-natural-hk",
        "automation_conversation_id": "conversation-natural-hk",
        "automation_run_kind": run_kind,
        "deployment_id": f"NORTHSTAR_D1_{market}",
        "market": market,
    }


def test_injects_verified_natural_run_identity_before_market_call(monkeypatch):
    observed = {}
    monkeypatch.setattr(
        workbuddy_entry,
        "resolve_workbuddy_provenance",
        lambda automation_id: _verified(),
    )

    def fake_main_for_market(market, argv):
        observed.update(
            {
                "market": market,
                "argv": argv,
                "source": os.environ.get("NORTHSTAR_INVOCATION_SOURCE"),
                "automation_id": os.environ.get("NORTHSTAR_AUTOMATION_ID"),
                "automation_run_id": os.environ.get(
                    "NORTHSTAR_AUTOMATION_RUN_ID"
                ),
                "conversation_id": os.environ.get(
                    "NORTHSTAR_AUTOMATION_CONVERSATION_ID"
                ),
                "run_kind": os.environ.get("NORTHSTAR_AUTOMATION_RUN_KIND"),
            }
        )
        return 0

    monkeypatch.setattr(workbuddy_entry, "main_for_market", fake_main_for_market)

    assert workbuddy_entry.run_scheduled_workbuddy_market(
        HK_AUTOMATION_ID,
        "HK",
        ["--no-write"],
    ) == 0
    assert observed == {
        "market": "HK",
        "argv": ["--no-write"],
        "source": "WORKBUDDY_AUTOMATION",
        "automation_id": HK_AUTOMATION_ID,
        "automation_run_id": "run-natural-hk",
        "conversation_id": "conversation-natural-hk",
        "run_kind": "scheduled",
    }
    for key in workbuddy_entry.PROVENANCE_ENV_KEYS:
        assert key not in os.environ


def test_accepts_verified_missed_recovery_before_market_call(monkeypatch):
    observed = {}
    monkeypatch.setattr(
        workbuddy_entry,
        "resolve_workbuddy_provenance",
        lambda automation_id: _verified(run_kind="missed"),
    )

    def fake_main_for_market(market, argv):
        observed.update(
            {
                "market": market,
                "run_kind": os.environ.get("NORTHSTAR_AUTOMATION_RUN_KIND"),
            }
        )
        return 0

    monkeypatch.setattr(workbuddy_entry, "main_for_market", fake_main_for_market)

    assert workbuddy_entry.run_scheduled_workbuddy_market(
        HK_AUTOMATION_ID,
        "HK",
        [],
    ) == 0
    assert observed == {"market": "HK", "run_kind": "missed"}
    for key in workbuddy_entry.PROVENANCE_ENV_KEYS:
        assert key not in os.environ


def test_fails_before_market_when_provenance_is_unavailable(monkeypatch):
    market_called = False
    monkeypatch.setattr(
        workbuddy_entry,
        "resolve_workbuddy_provenance",
        lambda automation_id: {
            "provenance_status": "NOT_PROVIDED",
            "reason": "AUTOMATION_NOT_CURRENTLY_RUNNING",
        },
    )

    def fake_main_for_market(market, argv):
        nonlocal market_called
        market_called = True
        return 0

    monkeypatch.setattr(workbuddy_entry, "main_for_market", fake_main_for_market)

    assert workbuddy_entry.run_scheduled_workbuddy_market(
        HK_AUTOMATION_ID,
        "HK",
        [],
    ) == 4
    assert market_called is False


def test_accepts_verified_manual_test_run_before_market_call(monkeypatch):
    observed = {}
    monkeypatch.setattr(
        workbuddy_entry,
        "resolve_workbuddy_provenance",
        lambda automation_id: _verified(run_kind="manual_test"),
    )

    def fake_main_for_market(market, argv):
        observed["run_kind"] = os.environ.get("NORTHSTAR_AUTOMATION_RUN_KIND")
        return 0

    monkeypatch.setattr(workbuddy_entry, "main_for_market", fake_main_for_market)

    assert workbuddy_entry.run_scheduled_workbuddy_market(
        HK_AUTOMATION_ID,
        "HK",
        [],
    ) == 0
    assert observed == {"run_kind": "manual_test"}


def test_accepts_verified_on_demand_followup_before_market_call(monkeypatch):
    observed = {}
    monkeypatch.setattr(
        workbuddy_entry,
        "resolve_workbuddy_provenance",
        lambda automation_id: _verified(run_kind="on_demand_followup"),
    )

    def fake_main_for_market(market, argv):
        observed["run_kind"] = os.environ.get("NORTHSTAR_AUTOMATION_RUN_KIND")
        return 0

    monkeypatch.setattr(workbuddy_entry, "main_for_market", fake_main_for_market)

    assert workbuddy_entry.run_scheduled_workbuddy_market(
        HK_AUTOMATION_ID,
        "HK",
        [],
    ) == 0
    assert observed == {"run_kind": "on_demand_followup"}


def test_rejects_unknown_run_kind_before_market_call(monkeypatch, capsys):
    market_called = False
    monkeypatch.setattr(
        workbuddy_entry,
        "resolve_workbuddy_provenance",
        lambda automation_id: _verified(run_kind="unknown"),
    )

    def fake_main_for_market(market, argv):
        nonlocal market_called
        market_called = True
        return 0

    monkeypatch.setattr(workbuddy_entry, "main_for_market", fake_main_for_market)

    assert workbuddy_entry.run_scheduled_workbuddy_market(
        HK_AUTOMATION_ID,
        "HK",
        [],
    ) == 4
    assert market_called is False
    assert "WORKBUDDY_RUN_KIND_NOT_ALLOWED" in capsys.readouterr().err


def test_rejects_cross_market_identity_before_market_call(monkeypatch):
    market_called = False
    monkeypatch.setattr(
        workbuddy_entry,
        "resolve_workbuddy_provenance",
        lambda automation_id: _verified(market="US"),
    )

    def fake_main_for_market(market, argv):
        nonlocal market_called
        market_called = True
        return 0

    monkeypatch.setattr(workbuddy_entry, "main_for_market", fake_main_for_market)

    assert workbuddy_entry.run_scheduled_workbuddy_market(
        HK_AUTOMATION_ID,
        "HK",
        [],
    ) == 4
    assert market_called is False
