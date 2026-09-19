from __future__ import annotations

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_research_package_does_not_import_production_or_execution_modules() -> None:
    forbidden_roots = {
        "core",
        "order_executor",
        "order_manager",
        "paper_trading",
        "unified_runner",
    }
    violations: list[str] = []
    for path in PACKAGE_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    if module.split(".")[0] in forbidden_roots:
                        violations.append(f"{path.name}:{module}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.split(".")[0] in forbidden_roots:
                    violations.append(f"{path.name}:{module}")
    assert not violations


def test_futu_adapter_imports_quote_only_capabilities() -> None:
    path = PACKAGE_ROOT / "futu_readonly.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "futu":
            imported.update(alias.name for alias in node.names)

    assert imported
    assert imported <= {
        "AuType",
        "KLType",
        "KL_FIELD",
        "OpenQuoteContext",
        "RET_OK",
    }


def test_formal_contract_documents_screen_provenance() -> None:
    contract = (
        PACKAGE_ROOT / "resources" / "INPUT_CONTRACTS.md"
    ).read_text(encoding="utf-8")
    assert "fetched_at_hkt" in contract
    assert "observed_at_hkt" in contract
    assert "daily_scores.csv" in contract


def test_paused_automation_uses_non_recurrent_status() -> None:
    sync = (PACKAGE_ROOT / "WORKBUDDY_SYNC.md").read_text(encoding="utf-8")
    assert "SKIPPED / DATA_ENGINEERING_PAUSED" in sync
    assert "STALE_INTRADAY_BAR` 只用于修正 2026-07-30" in sync
    assert "`selection_execution` 写 `NOT_RUN`" in sync
    assert "`candidate_count` 写 `N/A`" in sync
    assert "不得把未执行筛选表述为“0 只候选”" in sync


def test_automation_schedule_has_early_start_hard_guard() -> None:
    sync = (PACKAGE_ROOT / "WORKBUDDY_SYNC.md").read_text(encoding="utf-8")
    assert "文档版本：`v1.5`" in sync
    assert "当前状态：`ENABLED / RESEARCH_ONLY`" in sync
    assert "HSCI Top 50" in sync
    assert "adjustment=QFQ" in sync
    assert "每周一至周五 `16:35`" in sync
    assert "SKIPPED_EARLY / BEFORE_16_30_HKT" in sync
    assert "scheduled_for_hkt" in sync
    assert "started_at_hkt" in sync
    assert "finished_at_hkt" in sync
    assert "不得等待到 16:30" in sync
    assert "--main-report" in sync
    assert "不得另写、扩写、重排或二次计算主报告" in sync
    assert "selection_reason" in sync
    assert sync.index("### Step 0：系统时间硬闸") < sync.index(
        "当前状态若为 `DATA_ENGINEERING_PAUSED`"
    )
