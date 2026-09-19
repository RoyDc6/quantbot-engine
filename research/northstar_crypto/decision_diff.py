"""Read-only decision-layer diagnostics for Northstar Crypto variants.

The module consumes an archived ``artifact.json`` and its sibling ``input.json``.
It never calls market or account APIs and never opens a forward paper/demo ledger.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .config import Config
from .io_utils import digest, write_json
from .market import DAY_MS, validate_bars, validate_instrument
from .portfolio import empty_state, execute_batch, target_weights
from .strategy import Strategy
from .vendor import factors as factors


LEFT_VARIANT = "trend_only"
RIGHT_VARIANT = "trend_structure"
STRUCTURE_KEYS = (
    "bottom_structure",
    "bottom_structure_new",
    "top_structure",
    "top_structure_new",
)
TREND_EVENT_KEYS = (
    "cross_short_up",
    "cross_long_up",
    "cross_short_down",
    "cross_long_down",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _utc_date(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).date().isoformat()


def _structure_label(structure: dict[str, Any]) -> str:
    active = [key for key in STRUCTURE_KEYS if bool(structure.get(key))]
    return ",".join(active) if active else "NONE"


def _trend_event_label(trend: dict[str, Any]) -> str:
    active = [key for key in TREND_EVENT_KEYS if bool(trend.get(key))]
    return ",".join(active) if active else "NONE"


def _signed_filled_quantity(order: dict[str, Any]) -> float:
    quantity = float(order.get("filled_qty") or 0.0)
    if order.get("side") == "SELL":
        return -quantity
    if order.get("side") == "BUY":
        return quantity
    return 0.0


def classify(summary: dict[str, int]) -> str:
    """Classify the deepest layer reached by observable Structure differences."""

    if summary["trade_changed_count"]:
        return "STRUCTURE_REACHED_EXECUTION"
    if summary["target_weight_changed_count"]:
        return "TARGET_CHANGED_BUT_EXECUTION_FLATTENED"
    if summary["decision_changed_count"]:
        return "DECISION_CHANGED_BUT_RISK_ALLOCATOR_FLATTENED"
    if summary["structure_trigger_count"]:
        return "STRUCTURE_TRIGGERED_BUT_DECISION_UNCHANGED"
    return "NO_STRUCTURE_TRIGGERS_IN_WINDOW"


def diagnose(
    bars_by_symbol: dict[str, Any],
    instruments: dict[str, dict[str, Any]],
    strategy: Strategy,
    config: Config,
) -> dict[str, Any]:
    """Compare decision, target-weight, and simulated-trade paths by day/symbol."""

    if config.initial_cash is None:
        raise ValueError("DIAGNOSTIC_CAPITAL_REQUIRED")
    symbols = list(config.symbols)
    if set(bars_by_symbol) != set(symbols):
        raise ValueError("INCOMPLETE_BAR_UNIVERSE")
    if set(instruments) != set(symbols):
        raise ValueError("INCOMPLETE_INSTRUMENT_UNIVERSE")

    dates = [int(value) for value in bars_by_symbol[symbols[0]]["ts"]]
    if len(dates) <= config.warmup:
        raise ValueError("NO_BARS_AFTER_WARMUP")
    if any([int(value) for value in bars_by_symbol[symbol]["ts"]] != dates for symbol in symbols):
        raise ValueError("REPLAY_CALENDAR_MISMATCH")

    frames = {symbol: strategy.frames(bars_by_symbol[symbol]) for symbol in symbols}
    states = {
        LEFT_VARIANT: empty_state(config),
        RIGHT_VARIANT: empty_state(config),
    }
    rows: list[dict[str, Any]] = []

    for index in range(config.warmup - 1, len(dates) - 1):
        signals = {
            variant: [
                strategy.signal(
                    bars_by_symbol[symbol],
                    symbol,
                    variant,
                    frames[symbol],
                    index,
                )
                for symbol in symbols
            ]
            for variant in (LEFT_VARIANT, RIGHT_VARIANT)
        }
        quotes = {
            symbol: {
                "bid": float(bars_by_symbol[symbol].iloc[index + 1]["open"]),
                "ask": float(bars_by_symbol[symbol].iloc[index + 1]["open"]),
                "ts": int(dates[index + 1]),
            }
            for symbol in symbols
        }
        marks = {symbol: quote["bid"] for symbol, quote in quotes.items()}
        targets: dict[str, dict[str, float]] = {}
        results: dict[str, dict[str, Any]] = {}
        next_states: dict[str, dict[str, Any]] = {}

        for variant in (LEFT_VARIANT, RIGHT_VARIANT):
            _, targets[variant] = target_weights(
                signals[variant], states[variant], marks, config
            )
            next_states[variant], results[variant] = execute_batch(
                states[variant], signals[variant], quotes, instruments, config
            )

        left_signals = {row["symbol"]: row for row in signals[LEFT_VARIANT]}
        right_signals = {row["symbol"]: row for row in signals[RIGHT_VARIANT]}
        left_orders = {row["symbol"]: row for row in results[LEFT_VARIANT]["orders"]}
        right_orders = {row["symbol"]: row for row in results[RIGHT_VARIANT]["orders"]}

        for symbol in symbols:
            trend = factors.get_trend_state(frames[symbol][0].iloc[: index + 1])
            structure = factors.get_structure_state(frames[symbol][1].iloc[: index + 1])
            left_signal = left_signals[symbol]
            right_signal = right_signals[symbol]
            left_order = left_orders[symbol]
            right_order = right_orders[symbol]
            lot_size = float(instruments[symbol]["lotSz"])
            left_fill = _signed_filled_quantity(left_order)
            right_fill = _signed_filled_quantity(right_order)
            action_changed = left_signal["action"] != right_signal["action"]
            fraction_changed = not math.isclose(
                float(left_signal["raw_fraction"]),
                float(right_signal["raw_fraction"]),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            target_changed = not math.isclose(
                float(targets[LEFT_VARIANT][symbol]),
                float(targets[RIGHT_VARIANT][symbol]),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            trade_changed = not math.isclose(
                left_fill,
                right_fill,
                rel_tol=0.0,
                abs_tol=max(lot_size / 2.0, 1e-12),
            )
            structure_triggered = any(bool(structure.get(key)) for key in STRUCTURE_KEYS)
            rows.append(
                {
                    "signal_date_utc": _utc_date(dates[index]),
                    "execution_date_utc": _utc_date(dates[index + 1]),
                    "signal_close_ms": int(left_signal["signal_close_ms"]),
                    "symbol": symbol,
                    "trend_market": trend.get("market", "UNKNOWN"),
                    "trend_event": _trend_event_label(trend),
                    "structure_state": _structure_label(structure),
                    "structure_triggered": structure_triggered,
                    "trend_only_action": left_signal["action"],
                    "trend_only_fraction": float(left_signal["raw_fraction"]),
                    "trend_structure_action": right_signal["action"],
                    "trend_structure_fraction": float(right_signal["raw_fraction"]),
                    "action_changed": action_changed,
                    "fraction_changed": fraction_changed,
                    "decision_changed": action_changed or fraction_changed,
                    "trend_only_target_weight": float(targets[LEFT_VARIANT][symbol]),
                    "trend_structure_target_weight": float(targets[RIGHT_VARIANT][symbol]),
                    "target_weight_changed": target_changed,
                    "trend_only_order_status": left_order["status"],
                    "trend_structure_order_status": right_order["status"],
                    "trend_only_signed_fill_qty": left_fill,
                    "trend_structure_signed_fill_qty": right_fill,
                    "trade_status_changed": left_order["status"] != right_order["status"],
                    "trade_changed": trade_changed,
                    "structure_effect": right_signal["audit"]["structure"]["effect"],
                    "structure_fraction_delta": float(
                        right_signal["audit"]["structure"]["fraction_delta"]
                    ),
                }
            )

        states = next_states

    summary = {
        "row_count": len(rows),
        "evaluation_days": len(dates) - config.warmup,
        "symbol_count": len(symbols),
        "signal_start_date_utc": rows[0]["signal_date_utc"],
        "signal_end_date_utc": rows[-1]["signal_date_utc"],
        "structure_trigger_count": sum(row["structure_triggered"] for row in rows),
        "action_changed_count": sum(row["action_changed"] for row in rows),
        "fraction_changed_count": sum(row["fraction_changed"] for row in rows),
        "decision_changed_count": sum(row["decision_changed"] for row in rows),
        "target_weight_changed_count": sum(
            row["target_weight_changed"] for row in rows
        ),
        "trade_changed_count": sum(row["trade_changed"] for row in rows),
        "trade_status_changed_count": sum(
            row["trade_status_changed"] for row in rows
        ),
    }
    summary["classification"] = classify(summary)

    by_symbol = []
    for symbol in symbols:
        subset = [row for row in rows if row["symbol"] == symbol]
        by_symbol.append(
            {
                "symbol": symbol,
                "rows": len(subset),
                "structure_triggers": sum(row["structure_triggered"] for row in subset),
                "action_changes": sum(row["action_changed"] for row in subset),
                "fraction_changes": sum(row["fraction_changed"] for row in subset),
                "target_weight_changes": sum(
                    row["target_weight_changed"] for row in subset
                ),
                "trade_changes": sum(row["trade_changed"] for row in subset),
            }
        )

    structure_effect_counts = {
        effect: sum(row["structure_effect"] == effect for row in rows)
        for effect in sorted({row["structure_effect"] for row in rows})
    }

    unique_keys = {
        (row["signal_close_ms"], row["symbol"])
        for row in rows
    }
    quality_checks = {
        "complete_expected_rows": len(rows)
        == summary["evaluation_days"] * summary["symbol_count"],
        "unique_date_symbol_grain": len(unique_keys) == len(rows),
        "calendar_aligned": True,
        "utc_daily_input": all(timestamp % DAY_MS == 0 for timestamp in dates),
        "finite_fractions_and_weights": all(
            math.isfinite(float(row[key]))
            for row in rows
            for key in (
                "trend_only_fraction",
                "trend_structure_fraction",
                "trend_only_target_weight",
                "trend_structure_target_weight",
            )
        ),
    }
    quality_checks["all_pass"] = all(quality_checks.values())
    return {
        "schema_version": 1,
        "comparison": f"{LEFT_VARIANT}_vs_{RIGHT_VARIANT}",
        "definitions": {
            "grain": "one completed UTC daily signal date per symbol",
            "structure_trigger": (
                "any confirmed bottom/top structure or new confirmed structure "
                "used by the decision model"
            ),
            "decision_changed": "action changed or raw_fraction changed",
            "target_weight_changed": (
                "desired weight changed after single-name/gross caps and path state"
            ),
            "trade_changed": (
                "signed simulated filled quantity changed after lot/min-size rules"
            ),
        },
        "summary": summary,
        "by_symbol": by_symbol,
        "structure_effect_counts": structure_effect_counts,
        "quality_checks": quality_checks,
        "rows": rows,
    }


def load_archived_run(
    artifact_path: Path, capital_override: float | None = None
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    Config,
    Strategy,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    artifact_path = artifact_path.resolve()
    input_path = artifact_path.with_name("input.json")
    artifact = _read_json(artifact_path)
    snapshot = _read_json(input_path)
    if digest(snapshot) != artifact.get("snapshot_hash"):
        raise ValueError("SNAPSHOT_HASH_MISMATCH")
    if snapshot.get("source") != "OKX_PUBLIC_REST" or snapshot.get("bar") != "1Dutc":
        raise ValueError("INVALID_ARCHIVED_SOURCE_CONTRACT")
    if not snapshot.get("clock_ok"):
        raise ValueError("ARCHIVED_CLOCK_CHECK_FAILED")

    config_payload = dict(artifact["config"])
    config_payload["symbols"] = tuple(config_payload["symbols"])
    capital = capital_override
    if capital is None:
        capital = (
            artifact.get("funding", {})
            .get("account", {})
            .get("total_equity_usdt")
        )
    if capital is None or not math.isfinite(float(capital)) or float(capital) <= 0:
        raise ValueError("DIAGNOSTIC_CAPITAL_REQUIRED")
    config = replace(Config(**config_payload), initial_cash=float(capital))
    strategy = Strategy(config)
    if strategy.vendor_hash != artifact.get("vendor_hash"):
        raise ValueError("VENDOR_HASH_MISMATCH")

    if set(snapshot.get("symbols", {})) != set(config.symbols):
        raise ValueError("SNAPSHOT_UNIVERSE_MISMATCH")
    bars_by_symbol = {}
    instruments = {}
    for symbol in config.symbols:
        row = snapshot["symbols"][symbol]
        if row.get("status") != "CAPTURED":
            raise ValueError(f"CAPTURE_MISSING:{symbol}")
        bars_by_symbol[symbol] = validate_bars(
            row["candles"], snapshot["server_ms"], minimum=config.warmup
        )
        validate_instrument(symbol, row["instrument"])
        instruments[symbol] = row["instrument"]
    metadata = {
        "source_artifact": str(artifact_path),
        "source_input": str(input_path),
        "source_run_id": artifact.get("run_id"),
        "snapshot_captured_at": snapshot.get("captured_at"),
        "snapshot_hash": artifact.get("snapshot_hash"),
        "vendor_hash": strategy.vendor_hash,
        "capital_usdt": float(capital),
        "mode": "ARCHIVED_INPUT_READ_ONLY_DIAGNOSTIC",
        "network_access": False,
        "account_access": False,
        "orders_enabled": False,
    }
    return artifact, snapshot, config, strategy, bars_by_symbol, instruments, metadata


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    metadata = payload["metadata"]
    lines = [
        "# Northstar Crypto decision-diff 诊断",
        "",
        f"- 来源运行：`{metadata['source_run_id']}`",
        f"- 快照时间：`{metadata['snapshot_captured_at']}`",
        "- 对照：`trend_only` vs `trend_structure`",
        "- 模式：归档输入只读诊断；无网络、无账户访问、无订单",
        f"- 数据范围：{summary['evaluation_days']} 个决策日 × {summary['symbol_count']} 个标的 = {summary['row_count']} 行",
        f"- 信号日期：{summary['signal_start_date_utc']} 至 {summary['signal_end_date_utc']}（UTC）",
        f"- 判定：`{summary['classification']}`",
        "",
        "## 五层统计",
        "",
        "| 层级 | 次数 |",
        "|---|---:|",
        f"| Structure 触发 | {summary['structure_trigger_count']} |",
        f"| 改变 action | {summary['action_changed_count']} |",
        f"| 改变 fraction | {summary['fraction_changed_count']} |",
        f"| 改变最终 target_weight | {summary['target_weight_changed_count']} |",
        f"| 改变模拟成交数量 | {summary['trade_changed_count']} |",
        "",
        "## 分标的统计",
        "",
        "| 标的 | Structure触发 | action变化 | fraction变化 | target变化 | 成交变化 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["by_symbol"]:
        lines.append(
            f"| {row['symbol']} | {row['structure_triggers']} | "
            f"{row['action_changes']} | {row['fraction_changes']} | "
            f"{row['target_weight_changes']} | {row['trade_changes']} |"
        )
    lines.extend(
        [
            "",
            "## Structure 效果分类",
            "",
            "| 效果 | 日期×标的次数 |",
            "|---|---:|",
        ]
    )
    for effect, count in payload["structure_effect_counts"].items():
        lines.append(f"| {effect} | {count} |")
    lines.extend(
        [
            "",
            "## 解释规则",
            "",
            "- Structure 触发限定为决策模型使用的已确认 bottom/top structure；未确认 plateau 不计入 18 次触发。",
            "- `STRUCTURE_TRIGGERED_BUT_DECISION_UNCHANGED`：结构状态出现，但没有改变 action/fraction。",
            "- `DECISION_CHANGED_BUT_RISK_ALLOCATOR_FLATTENED`：决策发生变化，但最终目标仓位相同。",
            "- `TARGET_CHANGED_BUT_EXECUTION_FLATTENED`：目标仓位不同，但受取整、最小订单等影响，模拟成交相同。",
            "- `STRUCTURE_REACHED_EXECUTION`：结构差异最终改变了模拟成交路径。",
            "",
            "## 数据质量",
            "",
        ]
    )
    for key, value in payload["quality_checks"].items():
        lines.append(f"- `{key}`：`{str(value).lower()}`")
    lines.extend(
        [
            "",
            "## 限制",
            "",
            "本诊断使用归档行情、当前已钉住的 Northstar vendor 和历史回放成交假设。它只回答 Structure 差异进入了哪一层，不构成样本外收益验证或真实资金授权。",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(output_dir: Path, payload: dict[str, Any]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "decision_diff.json"
    csv_path = output_dir / "decision_diff.csv"
    markdown_path = output_dir / "decision_diff.md"
    manifest_path = output_dir / "manifest.json"
    write_json(json_path, payload)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(payload["rows"][0]))
        writer.writeheader()
        writer.writerows(payload["rows"])
    markdown_path.write_text(_render_markdown(payload), encoding="utf-8")
    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (json_path, csv_path, markdown_path)
    }
    write_json(manifest_path, {"schema_version": 1, "files": hashes})
    return {
        "markdown": str(markdown_path),
        "csv": str(csv_path),
        "json": str(json_path),
        "manifest": str(manifest_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only trend_only vs trend_structure decision-diff diagnostic"
    )
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capital", type=float)
    args = parser.parse_args(argv)
    (
        _,
        _,
        config,
        strategy,
        bars_by_symbol,
        instruments,
        metadata,
    ) = load_archived_run(args.artifact, args.capital)
    payload = diagnose(bars_by_symbol, instruments, strategy, config)
    payload["metadata"] = metadata
    paths = write_outputs(args.output, payload)
    print(json.dumps({"summary": payload["summary"], "paths": paths}, ensure_ascii=False, indent=2))
    return 0 if payload["quality_checks"]["all_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
