"""Create a non-overwriting corrected delivery view from immutable run JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from research.northstar_d1.runner import OUTPUT_ROOT, render_markdown


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def create_correction(market: str, output_root: Path = OUTPUT_ROOT) -> dict:
    market = market.upper()
    market_root = Path(output_root) / market.lower()
    state = json.loads(
        (market_root / ".runtime" / "latest.json").read_text(encoding="utf-8")
    )
    run_id = str(state["run_id"])
    source_json = Path(state["artifacts"]["archive_json"])
    source_markdown = Path(state["artifacts"]["archive_markdown"])
    source_manifest = Path(state["artifacts"]["archive_manifest"])
    json_hash = _sha(source_json)
    markdown_hash = _sha(source_markdown)
    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    if json_hash != str(manifest["sha256"]["json"]).upper():
        raise RuntimeError("SOURCE_JSON_HASH_MISMATCH")
    if markdown_hash != str(manifest["sha256"]["markdown"]).upper():
        raise RuntimeError("SOURCE_MARKDOWN_HASH_MISMATCH")

    payload = json.loads(source_json.read_text(encoding="utf-8"))
    corrected = render_markdown(payload)
    for item in payload.get("signals") or []:
        symbol = str(item.get("symbol") or "")
        name = str(item.get("name") or "")
        if not name or name == symbol:
            raise RuntimeError(f"COMPANY_NAME_MISSING:{symbol}")
        if corrected.count(f"| {symbol} | {name} |") < 4:
            raise RuntimeError(f"COMPANY_NAME_TABLE_COVERAGE_INCOMPLETE:{symbol}")

    correction_root = market_root / "delivery_corrections" / run_id
    correction_root.mkdir(parents=True, exist_ok=True)
    corrected_path = correction_root / f"{source_markdown.stem}.corrected.md"
    corrected_manifest = correction_root / f"{source_markdown.stem}.corrected.manifest.json"
    corrected_bytes = corrected.encode("utf-8")
    corrected_hash = hashlib.sha256(corrected_bytes).hexdigest().upper()
    if corrected_path.exists() or corrected_manifest.exists():
        if corrected_path.is_file() and _sha(corrected_path) == corrected_hash:
            return {
                "status": "ALREADY_EXISTS",
                "report_path": str(corrected_path),
                "report_sha256": corrected_hash,
            }
        raise RuntimeError("DELIVERY_CORRECTION_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT")
    corrected_path.write_bytes(corrected_bytes)
    correction_record = {
        "schema_version": "1.0",
        "correction_kind": "DELIVERY_PRESENTATION_COMPLETE",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_run_id": run_id,
        "source_json": str(source_json),
        "source_json_sha256": json_hash,
        "source_markdown": str(source_markdown),
        "source_markdown_sha256": markdown_hash,
        "corrected_markdown": str(corrected_path),
        "corrected_markdown_sha256": corrected_hash,
    }
    corrected_manifest.write_text(
        json.dumps(correction_record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "CREATED",
        "report_path": str(corrected_path),
        "report_sha256": corrected_hash,
        "manifest_path": str(corrected_manifest),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True, choices=("HK", "US"))
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args()
    print(
        json.dumps(
            create_correction(args.market, args.output_root), ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
