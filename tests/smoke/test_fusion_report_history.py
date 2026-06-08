import json
import sys
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
    assert "VP/LLM 偏空驱动" in report
    assert "REDUCED 由 XMM=0 导致" not in report
    assert "账户现价来自 Futu 持仓查询" in report
    assert "| 标的 | 股数 | 成本价 | 账户现价 |" in report
    matrix_row = next(line for line in report.splitlines() if line.startswith("| 1 | 01810.HK"))
    assert matrix_row.count("|") == 20
