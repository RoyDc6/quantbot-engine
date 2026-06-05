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
