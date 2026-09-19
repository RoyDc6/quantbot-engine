import pytest
from pathlib import Path

from research.northstar_d1_futu_sim.runner import _reserve_source, build_orders, run


def _payload(intents):
    return {
        "market": "US",
        "model_version": "1.0.0",
        "signal_asof": "2026-09-15",
        "run": {"run_id": "RUN-1"},
        "paper_intents": intents,
    }


def test_buy_targets_account_fraction_and_uses_delta():
    payload = _payload([{
        "symbol": "AMD.US", "action": "BUY", "risk_capped_fraction": 0.2,
        "fraction_semantics": "desired_long_exposure",
    }])
    orders, skipped = build_orders(
        payload,
        {"total_assets": 100_000},
        [{"symbol": "AMD.US", "qty": 10, "can_sell_qty": 10}],
        {"AMD.US": {"price": 100, "lot_size": 1}},
    )
    assert orders[0]["qty"] == 190
    assert skipped == []


def test_sell_is_capped_by_sellable_position_and_lot():
    payload = _payload([{
        "symbol": "0700.HK", "action": "SELL", "risk_capped_fraction": 0.4,
        "fraction_semantics": "fraction_of_existing_position_to_reduce",
    }])
    payload["market"] = "HK"
    orders, skipped = build_orders(
        payload,
        {"total_assets": 100_000},
        [{"symbol": "0700.HK", "qty": 1000, "can_sell_qty": 300}],
        {"0700.HK": {"price": 300, "lot_size": 100}},
    )
    assert orders[0]["qty"] == 300
    assert skipped == []


def test_sell_without_position_is_skipped():
    payload = _payload([{
        "symbol": "AMZN.US", "action": "SELL", "risk_capped_fraction": 0.4,
        "fraction_semantics": "fraction_of_existing_position_to_reduce",
    }])
    orders, skipped = build_orders(
        payload, {"total_assets": 100_000}, [],
        {"AMZN.US": {"price": 250, "lot_size": 1}},
    )
    assert orders == []
    assert skipped[0]["reason"] == "NO_POSITION_DELTA_OR_BELOW_LOT"


def test_source_run_can_only_be_reserved_once(tmp_path, monkeypatch):
    monkeypatch.setattr("research.northstar_d1_futu_sim.runner.CONSUMED", tmp_path)
    marker, first = _reserve_source("US", "RUN-1", "ABC")
    same_marker, second = _reserve_source("US", "RUN-1", "ABC")
    assert first is True
    assert second is False
    assert same_marker == marker


def test_execution_requires_the_exact_integrated_run(monkeypatch, tmp_path):
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "research.northstar_d1_futu_sim.runner.select_latest_artifact",
        lambda market: (source, {"market": market}, {"run_id": "RUN-OLD"}),
    )
    with pytest.raises(RuntimeError, match="source run mismatch"):
        run("US", execute_sim=True, expected_run_id="RUN-NEW")


def test_unresolved_prior_order_blocks_new_submission(monkeypatch, tmp_path):
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    payload = {
        "market": "US",
        "validation_status": "NOT_EVALUATED",
        "paper_intents": [{
            "symbol": "AMD.US", "action": "BUY", "risk_capped_fraction": 0.2,
            "fraction_semantics": "desired_long_exposure",
        }],
    }
    manifest = {"run_id": "RUN-NEW"}

    class Query:
        def __init__(self, data):
            self.data = data
            self.ok = True

        def require(self, label):
            return self.data

    class Adapter:
        def test_connection(self, timeout):
            return True, "ok"

        def get_account_info(self, market):
            return Query({"total_assets": 100_000})

        def get_positions(self, market):
            return Query([])

        def fetch_quotes(self, symbols):
            return {"AMD.US": {"price": 100, "lot_size": 1}}

    monkeypatch.setattr(
        "research.northstar_d1_futu_sim.runner.select_latest_artifact",
        lambda market: (source, payload, manifest),
    )
    monkeypatch.setattr(
        "research.northstar_d1_futu_sim.runner.FutuAdapter", Adapter
    )
    monkeypatch.setattr(
        "research.northstar_d1_futu_sim.runner._reconcile_existing_orders",
        lambda market, adapter: [{"symbol": "AMD.US", "status": "SUBMITTED"}],
    )
    monkeypatch.setattr(
        "research.northstar_d1_futu_sim.runner.RECEIPTS", tmp_path / "receipts"
    )
    monkeypatch.setattr(
        "research.northstar_d1_futu_sim.runner.CONSUMED", tmp_path / "consumed"
    )

    receipt = run("US", execute_sim=True, expected_run_id="RUN-NEW")

    assert receipt["reconciliation"] == "ATTENTION_REQUIRED"
    assert receipt["source_reserved_this_run"] is False
    assert receipt["results"] == []
    assert receipt["executor_error"] == "UNRESOLVED_PRIOR_ORDERS_BLOCK_NEW_SUBMISSION"
    assert not (tmp_path / "consumed").exists()
