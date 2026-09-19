import json
from pathlib import Path

import pytest

from research.northstar_d1.workbuddy_receipt import (
    OUTPUT_ROOT,
    ReceiptValidationError,
    build_receipt_document,
)


AUTOMATIONS = {
    "HK": "automation-1785736457372",
    "US": "automation-1785736457815",
}


def _provenance(market: str) -> dict:
    latest = json.loads(
        (OUTPUT_ROOT / market.lower() / ".runtime" / "latest.json").read_text(
            encoding="utf-8"
        )
    )
    if latest.get("invocation_source") != "WORKBUDDY_AUTOMATION":
        pytest.skip(
            "legacy WorkBuddy receipt contract only applies to a current "
            "WORKBUDDY_AUTOMATION artifact"
        )
    kind = latest["automation_run_kind"]
    return {
        "provenance_status": "VERIFIED",
        "invocation_source": "WORKBUDDY_AUTOMATION",
        "automation_id": AUTOMATIONS[market],
        "automation_run_id": latest["automation_run_id"],
        "automation_conversation_id": latest["automation_conversation_id"],
        "automation_run_kind": kind,
        "automation_origin_run_kind": kind,
        "automation_identity_mode": "ACTIVE_RUNTIME",
        "deployment_id": f"NORTHSTAR_D1_{market}",
        "market": market,
    }


@pytest.mark.parametrize("market", ["HK", "US"])
def test_receipt_contains_full_contract_without_internal_reason_codes(market):
    document, run_id = build_receipt_document(
        AUTOMATIONS[market], market, _provenance(market)
    )

    assert run_id.startswith("run-")
    assert "当前 JSON" in document
    assert "归档清单" in document
    assert "结构审计（全标的）" in document
    assert "序列审计（全标的）" in document
    assert "| 门禁 | 结果 |" in document
    assert "| Gate | Result |" not in document
    assert "| 标的代码 | 公司名称 | 数据源 | 查询往返耗时（秒）" in document
    assert "| 标的代码 | 公司名称 | 候选动作→输出动作 |" in document
    assert "| 标的代码 | 公司名称 | 计数 | 阶段 |" in document
    assert "| Status |" not in document
    assert "成功（SUCCESS）" in document
    assert "富途 OpenD 实时直连（FUTU_OPEND_LIVE）" in document
    assert "RESEARCH_ONLY" in document
    assert "ZERO_EXECUTION" in document
    assert "reason_codes" not in document
    expected_rows = 7 if market == "HK" else 15
    assert document.count("FUTU_OPEND_LIVE") == expected_rows
    latest = json.loads(
        (OUTPUT_ROOT / market.lower() / ".runtime" / "latest.json").read_text(
            encoding="utf-8"
        )
    )
    artifact = json.loads(Path(latest["artifacts"]["archive_json"]).read_text(encoding="utf-8"))
    for signal in artifact["signals"]:
        assert f"| {signal['symbol']} | {signal['name']} |" in document


def test_receipt_fails_closed_on_provenance_mismatch():
    provenance = _provenance("US")
    provenance["automation_run_id"] = "run-not-the-artifact-run"

    with pytest.raises(ReceiptValidationError, match="AUTOMATION_RUN_ID_MISMATCH"):
        build_receipt_document(AUTOMATIONS["US"], "US", provenance)
