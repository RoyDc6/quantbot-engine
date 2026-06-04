# -*- coding: utf-8 -*-
"""
Smoke tests for core/paths.py and absolute-path script startup.

Verifies that:
  1. All core.paths constants derive from PROJECT_ROOT correctly
  2. Subdirectory scripts can be started from an absolute path
     (non-project cwd) without ModuleNotFoundError: core
  3. scanner2/hk_tech_scan.py import mechanism works at module level
"""

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import (
    PROJECT_ROOT,
    OUTPUT_DIR,
    CACHE_DIR,
    CACHE_US_DIR,
    EVENT_CACHE_DIR,
    DB_PATH,
    ML_ALPHA_DIR,
    SCANNER2_CACHE,
    SCANNER2_CACHE_US,
    SCANNER1_CACHE,
)

# ── 1. core.paths 基础路径断言 ──────────────────────────────────────


def test_all_paths_derive_from_project_root():
    """Every exported path is a child of PROJECT_ROOT."""
    paths = [
        OUTPUT_DIR,
        CACHE_DIR,
        CACHE_US_DIR,
        EVENT_CACHE_DIR,
        DB_PATH,
        ML_ALPHA_DIR,
        SCANNER2_CACHE,
        SCANNER2_CACHE_US,
        SCANNER1_CACHE,
    ]
    for p in paths:
        assert str(p).startswith(str(PROJECT_ROOT)), f"{p} not under PROJECT_ROOT"


def test_project_root_resolves_to_expected():
    expected = Path(__file__).resolve().parents[2]
    assert PROJECT_ROOT == expected
    assert (PROJECT_ROOT / "core" / "paths.py").is_file()
    assert (PROJECT_ROOT / "unified_runner.py").is_file()


def test_path_constants_are_path_objects():
    assert all(isinstance(p, Path) for p in [
        OUTPUT_DIR, CACHE_DIR, DB_PATH, EVENT_CACHE_DIR,
    ])


# ── 2. 子目录入口脚本从绝对路径启动（非项目 cwd） ─────────────────


PYTHON = sys.executable


def _run_script(script_path: str, cwd: str = "C:/") -> subprocess.CompletedProcess:
    """Run a Python script from a non-project cwd and return the result."""
    return _subprocess_run([PYTHON, script_path, "--help"], cwd=cwd)


def _subprocess_run(args: list, cwd: str = "C:/", inp: str | None = None) -> subprocess.CompletedProcess:
    """Wrapper around subprocess.run with UTF-8 safe encoding for Windows."""
    return subprocess.run(
        args,
        cwd=cwd,
        input=inp,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def test_db_query_help_from_absolute_path():
    """scripts/db_query.py --help from non-project cwd."""
    script = str(PROJECT_ROOT / "scripts" / "db_query.py")
    result = _run_script(script)
    assert result.returncode == 0, (
        f"db_query.py failed (rc={result.returncode})\n"
        f"stderr: {result.stderr.strip()}"
    )
    assert "usage:" in result.stdout, "Expected --help to print usage"
    # Confirm no ModuleNotFoundError
    assert "ModuleNotFoundError" not in result.stderr, (
        f"ModuleNotFoundError in stderr:\n{result.stderr.strip()}"
    )


def test_unified_runner_help_from_absolute_path():
    """unified_runner.py --help from non-project cwd."""
    script = str(PROJECT_ROOT / "unified_runner.py")
    result = _run_script(script)
    assert result.returncode in (0, 2), (
        f"unified_runner.py failed (rc={result.returncode})\n"
        f"stderr: {result.stderr.strip()}"
    )
    assert "ModuleNotFoundError" not in result.stderr, (
        f"ModuleNotFoundError in stderr:\n{result.stderr.strip()}"
    )


def test_unified_runner_shows_usage_from_non_project_cwd():
    """Verify unified_runner actually prints something meaningful."""
    script = str(PROJECT_ROOT / "unified_runner.py")
    result = _run_script(script)
    # argparse with missing required args exits 2 but still prints usage
    stderr_lower = result.stderr.lower()
    stdout_lower = result.stdout.lower()
    has_usage = "usage:" in stderr_lower or "usage:" in stdout_lower
    assert has_usage, (
        f"Expected usage in output\nstdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
    )


# ── 3. scanner2/hk_tech_scan 导入级验证（不触发真实行情） ─────────


def test_hk_tech_scan_import_mechanism():
    """
    Verify that scanner2/hk_tech_scan.py's import chain works
    (from core.paths import ...) from a non-project cwd.

    Uses subprocess with PROJECT_ROOT in sys.path to isolate
    module-level side effects (dir creation, API import attempts).
    """
    code = (
        "import sys\n"
        f"sys.path.insert(0, r'{PROJECT_ROOT}')\n"
        "from core.paths import PROJECT_ROOT, SCANNER2_CACHE\n"
        "from core.paths import OUTPUT_DIR as _OUTPUT_DIR\n"
        "print(f'PROJECT_ROOT={PROJECT_ROOT}')\n"
        "print(f'CACHE_DIR={SCANNER2_CACHE}')\n"
    )
    result = _subprocess_run([PYTHON, "-c", code])
    assert result.returncode == 0, (
        f"hk_tech_scan import chain failed (rc={result.returncode})\n"
        f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
    )
    assert "PROJECT_ROOT=" in result.stdout
    assert "CACHE_DIR=" in result.stdout


def test_hk_tech_scan_module_import_no_core_error():
    """Full module import of scanner2.hk_tech_scan from non-project cwd."""
    code = (
        "import sys\n"
        f"sys.path.insert(0, r'{PROJECT_ROOT}')\n"
        "import scanner2.hk_tech_scan as m\n"
        "print(f'CACHE_DIR={m.CACHE_DIR}')\n"
    )
    result = _subprocess_run([PYTHON, "-c", code])
    # Module-level side effects (futu/tickflow imports) may fail,
    # but must NOT fail due to ModuleNotFoundError on core.*
    if result.returncode != 0:
        assert "No module named 'core'" not in result.stderr, (
            f"ModuleNotFoundError: core still present!\n{result.stderr.strip()}"
        )
