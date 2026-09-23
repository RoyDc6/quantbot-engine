from research.northstar_crypto.report import render_demo


def test_demo_report_separates_hold_from_risk_rebalance_and_shows_benchmarks():
    artifact = {
        "run_id": "CRYPTO-TEST",
        "status": "PASS",
        "signals": [
            {
                "symbol": "BTC-USDT",
                "data_status": "VALID",
                "signal_close_ms": 1_700_000_000_000,
                "action": "HOLD",
                "raw_fraction": 0.2,
                "reason": "test",
            }
        ],
        "counts": {"HOLD": 1},
        "config": {"single_cap": 0.2, "gross_cap": 0.6},
        "funding": {"account": None, "error": "fixture"},
        "execution": {
            "status": "PASS",
            "new_orders": 0,
            "duplicate_suppressed": False,
            "reconciliation": {"status": "PASS"},
            "risk_resolved": True,
            "orders": [],
            "plans": [
                {
                    "symbol": "BTC-USDT",
                    "action": "HOLD",
                    "current_qty": 1.0,
                    "target_weight": 0.2,
                    "target_qty": 0.8,
                    "delta_qty": -0.2,
                    "status": "PLANNED",
                }
            ],
        },
        "comparison": {
            "evaluation_days": 179,
            "results": [
                {"variant": "structure_only", "total_return": 0.4245, "max_drawdown": -0.1, "simulated_fills": 4},
                {"variant": "structure_sequence", "total_return": 0.4245, "max_drawdown": -0.1, "simulated_fills": 4},
                {"variant": "northstar_full", "total_return": 0.4245, "max_drawdown": -0.1, "simulated_fills": 4},
            ],
            "benchmarks": {"cash_return": 0.0, "equal_weight_buy_hold_gross": 0.7938},
            "limitations": ["fixture limitation"],
        },
        "llm": {"status": "NOT_USED", "accepted": []},
        "quality_checks": {"demo_only": True},
        "snapshot_hash": "snapshot",
        "vendor_hash": "vendor",
    }

    report = render_demo(artifact)

    assert "RISK_CAP_REBALANCE（非 SELL 信号）" in report
    assert "五标的等权买入持有 | 79.38%" in report
    assert "Northstar full 相对等权买入持有差额：-36.93%" in report
    assert "三个变体结果完全相同" in report
    assert "限制：fixture limitation" in report
