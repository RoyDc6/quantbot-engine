"""Repository-level checks for files that must never be committed."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PROHIBITED_TRACKED_PATHS = {
    "config/tickflow_key.txt",
    "paper_trading/futu_bridge_log.json",
    "paper_trading/last_scan_report.txt",
    "paper_trading/portfolio.json",
    "research/.codebuddy/settings.local.json",
    "research/error_screenshot.png",
    "scanner2/progress.json",
    "scanner2/progress_HK.json",
    "scanner2/progress_v2_HK.json",
    "scanner2/universe_cache.json",
}


def _tracked_files() -> set[str]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()}


def test_local_credentials_and_runtime_state_are_not_tracked():
    tracked = _tracked_files()
    violations = sorted(PROHIBITED_TRACKED_PATHS & tracked)
    assert not violations, f"Local credentials/runtime state must not be tracked: {violations}"


def test_repository_secret_check_passes():
    proc = subprocess.run(
        [sys.executable, "scripts/check_secrets.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
