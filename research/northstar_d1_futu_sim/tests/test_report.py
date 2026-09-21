import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research.northstar_d1_futu_sim.report import _execution_section, build_report


def _research_artifacts(root: Path, market: str, run_id: str, verdict="RUN_PASS"):
    output = root / market.lower()
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / f"{market}.json"
    md_path = output / f"{market}.md"
    manifest_path = output / f"{market}.manifest.json"
    json_path.write_text(json.dumps({"market": market}), encoding="utf-8")
    md_path.write_text(
        f"# Northstar-D1 {market} 运行报告\n\n## 信号\n\n| 标的 | 动作 |\n|---|---|\n| AMD.US | BUY |\n",
        encoding="utf-8",
    )
    manifest = {
        "market": market,
        "run_id": run_id,
        "run_verdict": verdict,
        "sha256": {
            "json": hashlib.sha256(json_path.read_bytes()).hexdigest().upper(),
            "markdown": hashlib.sha256(md_path.read_bytes()).hexdigest().upper(),
        },
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path), "manifest": str(manifest_path)}


def _forward_receipt(root: Path, market: str, run_id: str, artifacts, **extra):
    root.mkdir(parents=True, exist_ok=True)
    data = {
        "mode": "NORTHSTAR_D1_INTEGRATED_FORWARD",
        "market": market,
        "status": extra.pop("status", "PASS"),
        "run_id": run_id,
        "research_artifacts": artifacts,
        **extra,
    }
    path = root / f"20260916T213500000000_{market.lower()}_forward.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _execution_receipt(root: Path, market: str, run_id: str, source: Path):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"20260916_{market.lower()}_execute.json"
    data = {
        "mode": "FUTU_SIM_FORWARD",
        "market": market,
        "real_trading_allowed": False,
        "source": {
            "path": str(source),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest().upper(),
            "manifest_run_id": run_id,
        },
        "reconciliation": "PASS",
        "account_before": {"total_assets": 100000, "cash": 90000},
        "account_after": {"total_assets": 99000, "cash": 80000},
        "post_execution_snapshot_at_utc": "2026-09-16T13:36:00+00:00",
        "cash_sizing": {
            "spendable_cash": 89100,
            "projected_buy_cost": 1000,
            "projected_cash_after": 88100,
        },
        "planned_orders": [{"symbol": "AMD.US", "action": "BUY", "qty": 10, "price": 100}],
        "results": [{"order_id": "1", "symbol": "AMD.US", "action": "BUY", "qty": 10, "price": 100, "status": "FILLED_ALL", "dealt_qty": 10, "dealt_avg_price": 100}],
        "positions_after": [{"symbol": "AMD.US", "qty": 10, "cost_price": 100, "market_val": 1000}],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_report_combines_same_run_signal_and_execution(tmp_path):
    market = "US"
    run_id = "RUN-US"
    artifacts = _research_artifacts(tmp_path / "northstar", market, run_id)
    execution = _execution_receipt(tmp_path / "receipts", market, run_id, Path(artifacts["json"]))
    _forward_receipt(
        tmp_path / "forward", market, run_id, artifacts,
        execution_receipt=str(execution), reconciliation="PASS",
    )
    result = build_report(
        market=market,
        forward_receipts_dir=tmp_path / "forward",
        receipts_dir=tmp_path / "receipts",
        reports_dir=tmp_path / "reports",
        northstar_output=tmp_path / "northstar",
        now=datetime(2026, 9, 16, 23, tzinfo=timezone.utc),
    )
    text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert result["status"] == "REPORT_READY"
    assert "## Northstar-D1 信号报告" in text
    assert "| AMD.US | BUY |" in text
    assert "## Futu 模拟执行与对账" in text
    assert "FILLED_ALL" in text
    assert "执行前模拟账户现金：90,000.00" in text
    assert "执行后模拟账户现金：80,000.00" in text
    assert "real_trading_allowed=false" in text


def test_execution_section_never_substitutes_before_account_for_missing_after_snapshot(tmp_path):
    lines = _execution_section(tmp_path / "receipt.json", {
        "reconciliation": "ATTENTION_REQUIRED",
        "account_before": {"total_assets": 100_000, "cash": 90_000},
        "account_after": None,
        "results": [{
            "order_id": "1", "symbol": "AMD.US", "action": "BUY", "qty": 10,
            "price": 100, "status": "SUBMITTED", "dealt_qty": 0,
        }],
        "unresolved_orders": [{
            "order_id": "1", "symbol": "AMD.US", "action": "BUY",
            "status": "SUBMITTED", "dealt_qty": 0,
        }],
    })
    text = "\n".join(lines)

    assert "执行后模拟账户现金：UNKNOWN" in text
    assert "不能用执行前现金替代" in text
    assert "存在未终态订单" in text
    assert "不会自动重发" in text


def test_failed_signal_report_never_falls_back_to_old_execution(tmp_path):
    market = "US"
    run_id = "RUN-US-FAILED"
    artifacts = _research_artifacts(
        tmp_path / "northstar", market, run_id, verdict="RUN_FAILED_CLOSED"
    )
    _forward_receipt(
        tmp_path / "forward", market, run_id, artifacts,
        status="FAILED_CLOSED", error="research run failed closed",
        execution_receipt=None,
    )
    result = build_report(
        market=market,
        forward_receipts_dir=tmp_path / "forward",
        receipts_dir=tmp_path / "receipts",
        reports_dir=tmp_path / "reports",
        northstar_output=tmp_path / "northstar",
        now=datetime(2026, 9, 16, 23, tzinfo=timezone.utc),
    )
    text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "RUN_FAILED_CLOSED" in text
    assert "NOT_EXECUTED" in text
    assert "本节不展示其他运行或历史订单" in text


def test_report_rejects_historical_forward_receipt(tmp_path):
    run_id = "RUN-US"
    artifacts = _research_artifacts(tmp_path / "northstar", "US", run_id)
    forward = tmp_path / "forward"
    forward.mkdir()
    (forward / "20260915T213500000000_us_forward.json").write_text(
        json.dumps({
            "mode": "NORTHSTAR_D1_INTEGRATED_FORWARD",
            "market": "US",
            "status": "PASS",
            "run_id": run_id,
            "research_artifacts": artifacts,
        }),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="no current integrated Forward receipt"):
        build_report(
            market="US",
            forward_receipts_dir=forward,
            receipts_dir=tmp_path / "receipts",
            reports_dir=tmp_path / "reports",
            northstar_output=tmp_path / "northstar",
            now=datetime(2026, 9, 16, 23, tzinfo=timezone.utc),
        )


def test_wrong_dst_noop_does_not_replace_open_window_receipt(tmp_path):
    run_id = "RUN-US"
    artifacts = _research_artifacts(tmp_path / "northstar", "US", run_id)
    _forward_receipt(
        tmp_path / "forward", "US", run_id, artifacts,
        status="FAILED_CLOSED", error="failed",
    )
    noop = tmp_path / "forward" / "20260916T223500000000_us_forward.json"
    noop.write_text(json.dumps({
        "mode": "NORTHSTAR_D1_INTEGRATED_FORWARD",
        "market": "US",
        "status": "MISSED_EXECUTION_WINDOW",
    }), encoding="utf-8")
    result = build_report(
        market="US",
        forward_receipts_dir=tmp_path / "forward",
        receipts_dir=tmp_path / "receipts",
        reports_dir=tmp_path / "reports",
        northstar_output=tmp_path / "northstar",
        now=datetime(2026, 9, 16, 23, tzinfo=timezone.utc),
    )
    assert result["forward_statuses"] == {"US": "FAILED_CLOSED"}


def test_single_market_report_uses_forward_market_session_date(tmp_path):
    run_id = "RUN-US-RECOVERY"
    artifacts = _research_artifacts(tmp_path / "northstar", "US", run_id)
    forward_path = _forward_receipt(
        tmp_path / "forward",
        "US",
        run_id,
        artifacts,
        status="FAILED_CLOSED",
        reconciliation="NOT_EXECUTED",
        window={"observed_at_local": "2026-09-16T22:39:22-04:00"},
    )
    forward_path.rename(
        forward_path.with_name("20260917T103922000000_us_forward.json")
    )

    result = build_report(
        market="US",
        forward_receipts_dir=tmp_path / "forward",
        receipts_dir=tmp_path / "receipts",
        reports_dir=tmp_path / "reports",
        northstar_output=tmp_path / "northstar",
        now=datetime(2026, 9, 17, 3, tzinfo=timezone.utc),
    )

    report_path = Path(result["report_path"])
    assert report_path.name == "2026-09-16_northstar_d1_futu_sim_us_daily.md"
    assert "（US）· 2026-09-16" in report_path.read_text(encoding="utf-8")
