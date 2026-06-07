import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.quant_db import QuantDB


def test_db_query_warns_when_quantdb_lags_signal_json(tmp_path):
    db_path = tmp_path / "quant.db"
    signal_dir = tmp_path / "signals"
    signal_dir.mkdir()

    db = QuantDB(db_path)
    try:
        db.conn.execute(
            "INSERT INTO signals(date, market, symbol) VALUES (?, ?, ?)",
            ("2026-05-21", "HK", "00700.HK"),
        )
        db.conn.execute(
            "INSERT INTO positions(date, market, symbol, shares) VALUES (?, ?, ?, ?)",
            ("2026-05-11", "HK", "00700.HK", 100),
        )
        db.conn.execute(
            "INSERT INTO trades(date, symbol, action, shares) VALUES (?, ?, ?, ?)",
            ("2026-05-18", "00700.HK", "BUY", 100),
        )
    finally:
        db.close()

    (signal_dir / "2026-06-06_HK.json").write_text(
        json.dumps({"signals": []}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "db_query.py"),
            "--db-path",
            str(db_path),
            "--signals-dir",
            str(signal_dir),
            "stats",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "[WARN] quant.db 数据落后" in result.stdout
    assert "signals最新=2026-05-21" in result.stdout
    assert "最新signal JSON=2026-06-06" in result.stdout
