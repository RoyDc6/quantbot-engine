import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from reports import fusion_report_v3


def _write_signals(path: Path, symbols: list[str]) -> None:
    payload = {
        "signals": [
            {
                "symbol": symbol,
                "fusion_score": float(idx),
                "fusion_level": "HOLD",
            }
            for idx, symbol in enumerate(symbols)
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_prev_signals_skips_duplicate_symbol_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(fusion_report_v3, "SIGNAL_DIR", tmp_path)

    _write_signals(
        tmp_path / "2026-06-04_HK.json",
        ["00700.HK"] * 7,
    )
    _write_signals(
        tmp_path / "2026-06-03_HK.json",
        ["00700.HK"] * 7,
    )
    _write_signals(
        tmp_path / "2026-06-02_HK.json",
        ["00700.HK", "09988.HK", "03690.HK"],
    )

    signals = fusion_report_v3._load_prev_signals("2026-06-05", "HK")

    assert [sig["symbol"] for sig in signals] == [
        "00700.HK",
        "09988.HK",
        "03690.HK",
    ]


def test_report_shows_gate_adjusted_position_and_all_reasons(tmp_path, monkeypatch):
    monkeypatch.setattr(fusion_report_v3, "SIGNAL_DIR", tmp_path / "signals")
    monkeypatch.setattr(fusion_report_v3, "REPORT_DIR", tmp_path / "reports")

    signals = [
        {
            "symbol": "01810.HK",
            "close": 27.12,
            "fusion_level": "REDUCED",
            "fusion_score": -21.5,
            "fusion_confidence": 0.53,
            "target_position": 0.2,
            "rsi_daily": 32.3,
            "rsi_weekly": 25.8,
            "weights_used": {"xmm": 0.6, "vp": 0.25, "llm": 0.15},
            "raw_scores": {"xmm": 0, "vp": -80, "llm": -10},
            "xmm_action": "HOLD",
            "xmm_reason": "缺乏信号，保持空仓",
            "xmm_trend": "DOWN",
            "vp_direction": "SELL",
            "vp_state": "below_box",
            "vp_poc": 32.06,
            "llm_summary": "slightly bearish",
            "gate_approved": False,
            "gate_reasons": [
                "置信度 0.53 < 0.60 (等级: REDUCED)",
                "融合得分 -21.5 < 30 (等级: REDUCED)",
            ],
        }
    ]

    report_path = fusion_report_v3.generate_v3_report(
        date="2026-06-08",
        market="HK",
        signals=signals,
        orders=[],
        positions=[
            {
                "symbol": "00700.HK",
                "qty": 100,
                "entry_price": 458.4,
                "current_price": 451.6,
            }
        ],
        account={"total_assets": 1505201, "cash": 1460041, "market_val": 45160},
        market_report={"market_state": "CRAB", "vix_regime": "CANDIDATE"},
    )

    report = Path(report_path).read_text(encoding="utf-8")

    assert "信号价" in report
    assert "目标仓位 | 有效仓位" in report
    assert "| 20% | 0%<br><sub>Gate拦截</sub>" in report
    assert "置信度 0.53 < 0.60" in report
    assert "融合得分 -21.5 < 30" in report
    assert "1/1 REDUCED（XMM中性子集）：0偏多 / 1偏空 / 0中性" in report
    assert "REDUCED 由 XMM=0 导致" not in report
    assert "账户现价来自 Futu 持仓查询" in report
    assert "| 标的 | 股数 | 成本价 | 账户现价 |" in report
    matrix_row = next(line for line in report.splitlines() if line.startswith("| 1 | 01810.HK"))
    assert matrix_row.count("|") == 20


def test_root_cause_checks_llm_before_calling_all_factors_neutral():
    cause = fusion_report_v3._extract_root_cause(
        {
            "fusion_level": "HOLD",
            "fusion_score": 6.0,
            "raw_scores": {"xmm": 0, "vp": 0, "llm": 40},
            "llm_status": "OK",
        }
    )

    assert "三因子均中性" not in cause
    assert "LLM单独偏多(+40)" in cause


def test_root_cause_labels_partial_llm_score_as_audit_only():
    cause = fusion_report_v3._extract_root_cause(
        {
            "fusion_level": "HOLD",
            "fusion_score": 0.0,
            "raw_scores": {"xmm": 0, "vp": 0, "llm": 40},
            "llm_status": "PARTIAL",
        }
    )

    assert "LLM异常[PARTIAL]" in cause
    assert "LLM审计分+40(未入融合)" in cause


def test_root_cause_and_consensus_treat_weight_zero_llm_as_audit_only():
    signal = {
        "fusion_level": "HOLD",
        "fusion_score": 0.0,
        "raw_scores": {"xmm": 0, "vp": 0, "llm": 40},
        "weights_used": {"xmm": 0.60, "vp": 0.25, "llm": 0.0},
        "reserved_weights": {"llm": 0.15},
        "llm_alpha_mode": "audit_only",
        "xmm_status": "OK",
        "vp_status": "OK",
        "llm_status": "OK",
    }

    cause = fusion_report_v3._extract_root_cause(signal)

    assert "LLM审计分+40(未入融合)" in cause
    assert "LLM单独偏多" not in cause
    assert fusion_report_v3._factor_consensus_label(signal) == "⬜有效因子中性"


def test_audit_only_report_copy_and_weight_policy_are_explicit():
    signal = {
        "symbol": "AAPL.US",
        "fusion_level": "REDUCED",
        "fusion_score": 20.0,
        "fusion_confidence": 0.38,
        "raw_scores": {"xmm": 0, "vp": 80, "llm": 40},
        "weights_used": {"xmm": 0.60, "vp": 0.25, "llm": 0.0},
        "reserved_weights": {"llm": 0.15},
        "llm_alpha_mode": "audit_only",
        "xmm_status": "OK",
        "xmm_action": "HOLD",
        "vp_status": "OK",
        "vp_direction": "BUY",
        "vp_state": "above_box",
        "llm_status": "OK",
        "llm_summary": "technical audit",
        "gate_approved": True,
    }

    views = fusion_report_v3._build_factor_view_section([signal])
    decomposition = fusion_report_v3._build_factor_decomposition([], [signal], [])
    notes = fusion_report_v3._build_strategy_notes([signal], "BULL", "US")

    assert "有效因子观点 + LLM审计" in views
    assert "有效因子一致性" in views
    assert "LLM仅保留审计亮牌" in views
    assert "（审计）" in views
    assert "有效因子加权分解 + LLM审计" in decomposition
    assert "LLM审计分" in decomposition
    assert "→alpha" in decomposition
    assert "LLM** (技术审计) | **0% alpha**" in decomposition
    assert "预留权重** | **15%**" in decomposition
    assert "LLM仅作审计" in notes
    assert "VP 和 LLM 无法独自驱动" not in notes


def test_fallback_route_is_visible_without_changing_audit_only_policy():
    signal = {
        "symbol": "00700.HK",
        "fusion_level": "HOLD",
        "fusion_score": -5.0,
        "fusion_confidence": 0.23,
        "raw_scores": {"xmm": 0, "vp": -20, "llm": -1},
        "weights_used": {"xmm": 0.60, "vp": 0.25, "llm": 0.0},
        "reserved_weights": {"llm": 0.15},
        "llm_alpha_mode": "audit_only",
        "xmm_status": "OK",
        "xmm_action": "HOLD",
        "vp_status": "OK",
        "vp_direction": "SELL",
        "llm_status": "FALLBACK",
        "llm_route_status": "FALLBACK",
        "llm_model": "fallback-model",
        "llm_summary": "fallback audit",
        "llm_event_type": "none",
        "gate_approved": True,
    }

    views = fusion_report_v3._build_factor_view_section([signal])
    notes = fusion_report_v3._build_strategy_notes([signal], "BEAR", "HK")
    detail = fusion_report_v3._get_llm_detail(signal)

    assert "回退审计" in views
    assert "FALLBACK" in views
    assert "1/1 标的 FALLBACK" in notes
    assert "实际模型 fallback-model" in notes
    assert detail.startswith("[FALLBACK]")


def test_consensus_and_root_cause_ignore_partial_factor():
    signal = {
        "fusion_level": "REDUCED",
        "fusion_score": 23.5,
        "raw_scores": {"xmm": 0, "vp": 80, "llm": 40},
        "weights_used": {"xmm": 0.7059, "vp": 0.2941, "llm": 0.0},
        "xmm_status": "OK",
        "xmm_action": "HOLD",
        "vp_status": "OK",
        "vp_direction": "BUY",
        "llm_status": "PARTIAL",
    }

    assert fusion_report_v3._factor_consensus_label(signal) == "🟢有效因子偏多(1/2)"
    cause = fusion_report_v3._extract_root_cause(signal)
    assert "有效因子VP偏多" in cause
    assert "VP+LLM" not in cause


def test_risk_dashboard_flags_single_position_overweight():
    dashboard = fusion_report_v3._build_risk_dashboard(
        positions=[
            {
                "symbol": "AAPL.US",
                "qty": 739,
                "entry_price": 267.89,
                "current_price": 294.42,
            }
        ],
        signals=[{"symbol": "AAPL.US", "fusion_level": "REDUCED"}],
        total_assets=1_018_090,
    )

    assert "超配告警 21.4% > 20%（不自动减仓）" in dashboard
    assert "单只目标/开仓上限 | 20% | 超配仅告警，不自动减仓" in dashboard


def test_strategy_notes_report_mixed_reduced_directions_exactly():
    signals = []
    for index, score in enumerate((23.8, 21.5, 21.5, -17.0, -17.0, -18.5, -18.5)):
        signals.append(
            {
                "symbol": f"TEST{index}.US",
                "fusion_level": "REDUCED",
                "fusion_score": score,
                "raw_scores": {"xmm": 0, "vp": 80 if score > 0 else -80, "llm": 10},
                "weights_used": {"xmm": 0.60, "vp": 0.25, "llm": 0.15},
                "xmm_status": "OK",
                "vp_status": "OK",
                "llm_status": "OK",
            }
        )

    notes = fusion_report_v3._build_strategy_notes(signals, "BULL", "US")

    assert "REDUCED 占比 100% (7/7)：3偏多 / 4偏空 / 0中性" in notes
    assert "多数标的" not in notes
    assert "7/7 REDUCED（XMM中性子集）：3偏多 / 4偏空 / 0中性" in notes
    assert "7/7 REDUCED：有效非XMM因子偏空" not in notes
    assert "本轮无通过 Gate 的 BUY/STRONG_BUY" in notes


def test_strategy_notes_treat_balanced_reduced_scores_as_neutral():
    signals = []
    for index, score in enumerate((20.0, 20.0, 20.0, -20.0, -20.0, -20.0)):
        signals.append(
            {
                "symbol": f"BALANCED{index}.US",
                "fusion_level": "REDUCED",
                "fusion_score": score,
                "raw_scores": {"xmm": 0, "vp": 80 if score > 0 else -80, "llm": 0},
                "weights_used": {"xmm": 0.60, "vp": 0.25, "llm": 0.0},
                "xmm_status": "OK",
                "vp_status": "OK",
                "llm_status": "OK",
            }
        )

    notes = fusion_report_v3._build_strategy_notes(signals, "BULL", "US")

    assert "REDUCED 占比 100% (6/6)：3偏多 / 3偏空 / 0中性" in notes
    assert "多数标的处于 SELL 阈值边缘" not in notes


def test_strategy_notes_report_current_us_reduced_mix_without_majority_claim():
    reduced_scores = (20.0, 20.0, 20.0, -20.0, -20.0)
    signals = [
        {
            "symbol": f"REDUCED{index}.US",
            "fusion_level": "REDUCED",
            "fusion_score": score,
            "raw_scores": {"xmm": 0, "vp": 80 if score > 0 else -80, "llm": 0},
            "weights_used": {"xmm": 0.60, "vp": 0.25, "llm": 0.0},
            "gate_approved": False,
        }
        for index, score in enumerate(reduced_scores)
    ]
    signals.extend(
        {
            "symbol": f"HOLD{index}.US",
            "fusion_level": "HOLD",
            "fusion_score": 5.0 if index < 5 else -5.0,
            "raw_scores": {"xmm": 0, "vp": 20, "llm": 0},
            "weights_used": {"xmm": 0.60, "vp": 0.25, "llm": 0.0},
            "gate_approved": True,
        }
        for index in range(10)
    )

    notes = fusion_report_v3._build_strategy_notes(signals, "BULL", "US")

    assert "REDUCED 占比 33% (5/15)：3偏多 / 2偏空 / 0中性" in notes
    assert "多数标的" not in notes
    assert "本轮无通过 Gate 的 BUY/STRONG_BUY" in notes


def test_strategy_notes_only_call_out_gate_approved_buys():
    blocked = {
        "symbol": "BLOCKED.US",
        "fusion_level": "BUY",
        "fusion_score": 40.0,
        "gate_approved": False,
        "raw_scores": {"xmm": 80, "vp": 20, "llm": 0},
    }
    approved = {
        **blocked,
        "symbol": "APPROVED.US",
        "gate_approved": True,
    }

    blocked_notes = fusion_report_v3._build_strategy_notes(
        [blocked], "BULL", "US"
    )
    approved_notes = fusion_report_v3._build_strategy_notes(
        [approved], "BULL", "US"
    )

    assert "本轮无通过 Gate 的 BUY/STRONG_BUY" in blocked_notes
    assert "不能仅凭市场状态建仓" in blocked_notes
    assert "本轮 1 只 BUY/STRONG_BUY 通过 Gate" in approved_notes
    assert "是否成交仍以 execution results 为准" in approved_notes


def test_header_exposes_cutoff_timezone_and_unverified_finality():
    generated_at = datetime(2026, 8, 4, 10, 23, tzinfo=timezone.utc)
    header = fusion_report_v3._build_header(
        "2026-08-04",
        "HK",
        "BULL",
        "CANDIDATE",
        "BULL",
        "NEUTRAL",
        0.8,
        {},
        signals=[{"date": "2026-08-04"}],
        generated_at=generated_at,
    )

    assert "信号数据截止** | 2026-08-04" in header
    assert "bar finality: UNVERIFIED" in header
    assert "2026-08-04T10:23:00+00:00" in header
    assert "NOT_COMPARABLE" in header


def test_root_cause_preserves_insufficient_data_counts():
    cause = fusion_report_v3._extract_root_cause(
        {
            "fusion_level": "HOLD",
            "fusion_score": 3.0,
            "raw_scores": {"xmm": 0, "vp": 0, "llm": 15},
            "weights_used": {"xmm": 0.80, "vp": 0.0, "llm": 0.20},
            "xmm_status": "OK",
            "vp_status": "NO_DATA",
            "llm_status": "OK",
            "warnings": [
                "LLM处于审计模式：原始分保留，alpha贡献为0",
                "VP: insufficient data: 118 < 120",
            ],
        }
    )

    assert "VP数据不足 118/120" in cause
    assert "VP数据不足 118/120" in fusion_report_v3._safe_cell_text(cause, 80)
    assert "LLM处于审计模式" not in fusion_report_v3._safe_cell_text(cause, 80)


def test_root_cause_prioritizes_nim_timeout_over_generic_audit_warning():
    cause = fusion_report_v3._extract_root_cause(
        {
            "fusion_level": "HOLD",
            "fusion_score": 5.0,
            "raw_scores": {"xmm": 0, "vp": 20, "llm": 0},
            "weights_used": {"xmm": 0.60, "vp": 0.25, "llm": 0.0},
            "xmm_status": "OK",
            "vp_status": "OK",
            "llm_status": "SKIPPED",
            "warnings": [
                "LLM处于审计模式：原始分保留，alpha贡献为0",
                "LLM: NVIDIA NIM 请求超时（8.0s）",
            ],
        }
    )

    assert "NVIDIA NIM 请求超时（8.0s）" in cause
    assert "LLM处于审计模式" not in cause


def test_shadow_confidence_excludes_partial_llm():
    conf_v2, _ = fusion_report_v3._compute_conf_v2_shadow(
        {
            "raw_scores": {"xmm": 0, "vp": 20, "llm": 100},
            "weights_used": {"xmm": 0.7059, "vp": 0.2941, "llm": 0.0},
            "xmm_status": "OK",
            "xmm_action": "HOLD",
            "xmm_position_size": 0.0,
            "vp_status": "OK",
            "vp_direction": "BUY",
            "llm_status": "PARTIAL",
            "llm_sentiment": 100,
            "fusion_confidence": 0.27,
        }
    )

    assert conf_v2 == 0.2


def test_execution_tracking_marks_blocked_sell_as_not_executed():
    report = fusion_report_v3._build_execution_tracking(
        orders=[{
            "symbol": "AAPL.US",
            "action": "SELL",
            "qty": 739,
            "price": 328.70,
            "reason": "止盈 pnl=+22.7%",
        }],
        positions=[{"symbol": "AAPL.US"}],
        signals=[],
        market="US",
        execution_mode="LIVE_BLOCKED_BY_RULES",
        execution_results=[],
    )

    assert "订单生成与执行状态" in report
    assert "未执行（风控规则拦截）" in report
    assert "卖出执行" not in report


def test_execution_tracking_calls_only_filled_all_a_real_fill():
    order = {
        "symbol": "AAPL.US",
        "action": "SELL",
        "qty": 739,
        "price": 328.70,
        "reason": "止盈",
    }
    filled = fusion_report_v3._build_execution_tracking(
        orders=[order],
        positions=[],
        signals=[],
        market="US",
        execution_mode="LIVE_CONFIRMED",
        execution_results=[{**order, "status": "FILLED_ALL"}],
    )
    failed = fusion_report_v3._build_execution_tracking(
        orders=[order],
        positions=[],
        signals=[],
        market="US",
        execution_mode="LIVE_CONFIRMED",
        execution_results=[{
            **order,
            "status": "FAILED",
            "message": "broker rejected",
        }],
    )

    assert "已成交" in filled
    assert "未成交（broker rejected）" in failed


def test_execution_tracking_labels_submitting_as_pending_confirmation():
    order = {
        "symbol": "AAPL.US",
        "action": "SELL",
        "qty": 739,
        "price": 333.35,
        "reason": "止盈",
    }
    report = fusion_report_v3._build_execution_tracking(
        orders=[order],
        positions=[{"symbol": "AAPL.US"}],
        signals=[],
        market="US",
        execution_mode="LIVE_CONFIRMED",
        execution_results=[{
            **order,
            "status": "SUBMITTING",
            "message": "order_id=8998116",
        }],
    )

    assert "已提交、待确认（order_id=8998116）" in report
    assert "未成交（order_id=8998116）" not in report
