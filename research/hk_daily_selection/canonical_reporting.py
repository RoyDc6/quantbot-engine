"""Deterministic canonical Markdown for the daily Hong Kong screen.

The canonical report is rendered only from selector output and explicit input
metadata.  It deliberately contains no discretionary market narrative so a
second writer cannot change ranks, metrics, or constraint reasons.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

from .config import StrategyConfig
from .reporting import _first_contrary_evidence
from .selector import candidates_as_of


def _first(frame: pd.DataFrame, column: str, default: object) -> object:
    if frame.empty or column not in frame:
        return default
    value = frame[column].iloc[0]
    return default if pd.isna(value) else value


def _pct(value: object, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value) * 100:.{digits}f}%"


def _number(value: object, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value):,.{digits}f}"


def _date_text(value: object) -> str:
    if value is None or pd.isna(value):
        return "—"
    return str(pd.Timestamp(value).date())


def _gate_text(value: bool | None) -> str:
    if value is True:
        return "成立"
    if value is False:
        return "不成立"
    return "不可计算"


def _selection_reason_text(value: object) -> str:
    code = "" if value is None or pd.isna(value) else str(value).strip()
    labels = {
        "selected_under_constraints": "按排名及约束入选",
        "sector_limit": "行业上限已满",
        "issuer_limit": "同发行人上限已满",
        "candidate_limit": "A 类名额已满",
        "market_gate": "市场门控不允许 A 类",
        "data_readiness": "数据未就绪，仅诊断",
        "survivorship_bias_not_publishable": "幸存者偏差模式，不可发布",
        "missing_completed_bar": "缺少已完成日线",
    }
    if code.startswith("hard_filter:"):
        return f"硬过滤：{code.removeprefix('hard_filter:')}"
    return labels.get(code, code or "未记录")


def _status_mark(actual: float, minimum: float) -> str:
    return "通过" if actual + 1e-12 >= minimum else "不足"


def _bool_from_comparison(
    left: object, right: object, operator: str = "gt"
) -> bool | None:
    if pd.isna(left) or pd.isna(right):
        return None
    if operator != "gt":
        raise ValueError(f"unsupported comparison: {operator}")
    return bool(float(left) > float(right))


def write_canonical_daily_report(
    scores: pd.DataFrame,
    as_of: pd.Timestamp,
    output_path: Path | str,
    config: StrategyConfig,
    *,
    source_posture: str,
    evidence_warnings: Iterable[str] = (),
    generated_at_hkt: object | None = None,
    input_metadata: Mapping[str, object] | None = None,
) -> Path:
    """Render the user-facing report from the same rows used for selection."""

    date = pd.Timestamp(as_of).normalize()
    dated = scores[pd.to_datetime(scores["date"]).dt.normalize() == date].copy()
    if dated.empty:
        raise ValueError(f"no scores for {date.date()}")

    candidates = candidates_as_of(scores, date)
    b_rows = dated[dated["priority"].eq("B")].sort_values(
        ["rank", "symbol"], na_position="last"
    )
    diagnostics = dated[dated["priority"].eq("Diagnostic")].sort_values(
        ["rank", "symbol"], na_position="last"
    )
    priority_counts = dated["priority"].value_counts()

    generated = (
        pd.Timestamp(generated_at_hkt)
        if generated_at_hkt is not None
        else pd.Timestamp.now(tz="Asia/Hong_Kong")
    )
    readiness = str(_first(dated, "data_readiness", "UNKNOWN"))
    formal_publish_allowed = bool(
        _first(dated, "formal_publish_allowed", False)
    )
    regime = str(_first(dated, "regime", "UNKNOWN"))
    breadth = _first(dated, "breadth_above_ma20", pd.NA)
    parent_size = int(_first(dated, "parent_universe_size", len(dated)))
    universe_size = int(_first(dated, "screen_universe_size", len(dated)))
    universe_definition = str(
        _first(dated, "universe_definition", "POINT_IN_TIME_MEMBERSHIP")
    )
    candidate_limit = {
        "ON": config.top_k,
        "CAUTION": config.caution_top_k,
    }.get(regime, 0)

    coverage_rows = [
        (
            "Top 50 排名证据",
            float(_first(dated, "universe_ranking_coverage_ratio", 0.0)),
            config.min_screen_universe_ranking_coverage,
        ),
        (
            "已完成日线",
            float(_first(dated, "bar_coverage_ratio", 0.0)),
            config.min_screen_bar_coverage_ratio,
        ),
        (
            "市场宽度样本",
            float(_first(dated, "breadth_coverage_ratio", 0.0)),
            config.min_screen_breadth_coverage_ratio,
        ),
        (
            "行业分类",
            float(_first(dated, "sector_coverage_ratio", 0.0)),
            config.min_screen_sector_coverage_ratio,
        ),
        (
            "显式停牌状态",
            float(_first(dated, "suspension_coverage_ratio", 0.0)),
            config.min_screen_suspension_coverage_ratio,
        ),
        (
            "公司行动证据",
            float(_first(dated, "corporate_action_coverage_ratio", 0.0)),
            config.min_screen_corporate_action_coverage_ratio,
        ),
    ]

    benchmark_close = _first(dated, "benchmark_close", pd.NA)
    benchmark_ma20 = _first(dated, "benchmark_ma_20", pd.NA)
    benchmark_ma60 = _first(dated, "benchmark_ma_60", pd.NA)
    breadth_count = int(_first(dated, "breadth_sample_count", 0))
    breadth_threshold = (
        config.breadth_on if regime == "ON" else config.breadth_caution
    )
    breadth_gate = (
        bool(float(breadth) >= breadth_threshold)
        if pd.notna(breadth)
        else None
    )

    adjustment_values = sorted(
        set(
            dated.get("adjustment", pd.Series(dtype=str))
            .dropna()
            .astype(str)
            .str.upper()
        )
    )
    adjustment = ", ".join(adjustment_values) or "N/A"
    fetch_values = pd.to_datetime(
        dated.get("fetched_at_hkt", pd.Series(dtype="object")),
        errors="coerce",
        utc=True,
    ).dropna()
    fetched_text = (
        fetch_values.max().tz_convert("Asia/Hong_Kong").isoformat()
        if not fetch_values.empty
        else "N/A"
    )

    snapshot = dated.get(
        "snapshot_is_suspended", pd.Series(pd.NA, index=dated.index)
    )
    suspension_known = int(snapshot.notna().sum())
    suspension_true = int(snapshot[snapshot.notna()].astype(bool).sum())
    suspension_false = suspension_known - suspension_true
    suspension_unknown = int(snapshot.isna().sum())
    volume = pd.to_numeric(
        dated.get("volume", pd.Series(pd.NA, index=dated.index)),
        errors="coerce",
    )
    zero_turnover_count = int((volume.notna() & (volume <= 0)).sum())

    lines = [
        f"# 香港市场每日优选研究 — {date.date()}",
        "",
        "> 研究候选，不是买入建议；不连接账户，不生成订单。",
        "> 本报告由筛选 CLI 从评分表同源确定性生成；禁止人工或模型二次改写数值、排名与原因。",
        "",
        "## 运行结论",
        "",
        f"- 生成时间：`{generated.isoformat()}`",
        f"- 运行状态：`{'SUCCESS / READY' if formal_publish_allowed else 'PARTIAL / DATA_READINESS'}`",
        f"- 数据就绪：`{readiness}`",
        f"- 正式候选发布：{'允许' if formal_publish_allowed else '禁止'}",
        f"- 研究股票池：`{universe_definition}`（{universe_size}/{parent_size}）",
        f"- 市场门控：`{regime}`",
        f"- A 类研究候选：{len(candidates)} 只（上限 {candidate_limit}）",
        f"- 证据状态：`{source_posture}`",
        f"- 价格口径：`{adjustment}`；当日证据最后抓取：`{fetched_text}`",
        "",
        "## 输入证据",
        "",
    ]

    metadata = dict(input_metadata or {})
    if metadata:
        lines.extend(["| 字段 | 值 |", "|---|---|"])
        for key, value in metadata.items():
            lines.append(f"| {key} | {value} |")
    else:
        lines.append("调用方未提供输入路径与哈希；评分表内来源字段仍保留。")

    lines.extend(
        [
            "",
            "## 数据覆盖",
            "",
            "| 维度 | 实际 | 最低阈值 | 状态 |",
            "|---|---:|---:|---|",
        ]
    )
    for label, actual, minimum in coverage_rows:
        lines.append(
            f"| {label} | {_pct(actual)} | {_pct(minimum)} | "
            f"{_status_mark(actual, minimum)} |"
        )

    lines.extend(
        [
            "",
            "## HSCI 市场门控",
            "",
            "| 条件 | 数值 | 判定 |",
            "|---|---:|---|",
            f"| HSCI Close > MA60 | {_number(benchmark_close)} > {_number(benchmark_ma60)} | {_gate_text(_bool_from_comparison(benchmark_close, benchmark_ma60))} |",
            f"| HSCI MA20 > MA60 | {_number(benchmark_ma20)} > {_number(benchmark_ma60)} | {_gate_text(_bool_from_comparison(benchmark_ma20, benchmark_ma60))} |",
            f"| Close > MA20 宽度 | {_pct(breadth)}（{breadth_count}/{universe_size}）；门槛 {_pct(breadth_threshold)} | {_gate_text(breadth_gate)} |",
            "",
            f"市场门控为 `{regime}`；A 类数量上限为 {candidate_limit}。",
            "",
            "## A 类研究候选",
            "",
        ]
    )

    if candidates.empty:
        if formal_publish_allowed:
            lines.append("当前门控或过滤条件下没有 A 类候选；允许空名单。")
        else:
            lines.append("数据未达到正式发布门槛，A 类候选强制为空；允许空名单。")
    else:
        lines.extend(
            [
                "| 入选序 | 因子原始排名 | 代码 | 名称 | 行业 | 综合分 | 动量 | 相对强度 | 趋势质量 | 价量确认 | 风险质量 | 入场质量 | 首要反证/待核验 |",
                "|---:|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for selection_order, (_, row) in enumerate(
            candidates.iterrows(), start=1
        ):
            lines.append(
                f"| {selection_order} | {row['rank']:.0f} | {row['symbol']} | "
                f"{row.get('name', '')} | {row.get('sector', '') or '未标注'} | "
                f"{row['composite_score']:.3f} | {row['momentum_score']:.3f} | "
                f"{row['relative_strength_score']:.3f} | {row['trend_quality_score']:.3f} | "
                f"{row['participation_score']:.3f} | {row['risk_score']:.3f} | "
                f"{row['entry_quality_score']:.3f} | {_first_contrary_evidence(row)} |"
            )

        lines.extend(
            [
                "",
                "### A 类原始指标（直接来自评分表）",
                "",
                "| 代码 | Close | MA20 | MA60 | 20日波动 | ATR20 | 距MA20/ATR | 5/20日成交额比 | 入场质量 |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for _, row in candidates.iterrows():
            lines.append(
                f"| {row['symbol']} | {_number(row.get('close'), 2)} | "
                f"{_number(row.get('ma_20'), 2)} | {_number(row.get('ma_60'), 2)} | "
                f"{_pct(row.get('realized_vol_20'))} | {_number(row.get('atr_20'), 4)} | "
                f"{_number(row.get('extension_atr_20'), 4)} | "
                f"{_number(row.get('turnover_ratio_5_20'), 3)} | "
                f"{_number(row.get('entry_quality_score'), 3)} |"
            )

    if not diagnostics.empty:
        lines.extend(
            [
                "",
                "## 诊断排名（不可发布为 A 类）",
                "",
                "> 以下仅用于验证因子链路；数据就绪前不是正式选股结果。",
                "",
                "| 诊断排名 | 代码 | 名称 | 综合分 | 决策原因 |",
                "|---:|---|---|---:|---|",
            ]
        )
        for _, row in diagnostics.head(max(config.top_k, 10)).iterrows():
            lines.append(
                f"| {row['rank']:.0f} | {row['symbol']} | {row.get('name', '')} | "
                f"{row['composite_score']:.3f} | "
                f"{_selection_reason_text(row.get('selection_reason'))} |"
            )

    if not b_rows.empty:
        lines.extend(
            [
                "",
                "## B 类观察与未入选原因",
                "",
                "| 因子原始排名 | 代码 | 名称 | 行业 | 综合分 | 第一约束原因 |",
                "|---:|---|---|---|---:|---|",
            ]
        )
        for _, row in b_rows.iterrows():
            lines.append(
                f"| {row['rank']:.0f} | {row['symbol']} | {row.get('name', '')} | "
                f"{row.get('sector', '') or '未标注'} | {row['composite_score']:.3f} | "
                f"{_selection_reason_text(row.get('selection_reason'))} |"
            )

    rejection_counts = (
        dated.loc[dated["priority"].eq("Reject"), "rejection_reason"]
        .str.split("|", regex=False)
        .explode()
        .value_counts()
    )
    lines.extend(
        [
            "",
            "## 候选漏斗与硬过滤",
            "",
            f"- 股票池记录：{len(dated)}",
            f"- 可评分：{int(dated['eligible'].sum())}",
            f"- A 类：{int(priority_counts.get('A', 0))}",
            f"- B 类观察：{int(priority_counts.get('B', 0))}",
            f"- C 类屏幕标记：{int(priority_counts.get('C', 0))}",
            f"- Reject：{int(priority_counts.get('Reject', 0))}",
            f"- Diagnostic：{int(priority_counts.get('Diagnostic', 0))}",
            f"- NotEvaluated（数据缺失）：{int(priority_counts.get('NotEvaluated', 0))}",
        ]
    )
    if not rejection_counts.empty:
        lines.extend(["", "主要硬过滤原因：", ""])
        for reason, count in rejection_counts.head(8).items():
            lines.append(f"- `{reason}`：{int(count)}")

    lines.extend(["", "## 公司行动与停牌证据", ""])
    if not candidates.empty:
        lines.extend(
            [
                "| A 类候选 | 状态 | 近90日行动 | 最近行动日期 |",
                "|---|---|---|---|",
            ]
        )
        for _, row in candidates.iterrows():
            recent = row.get("has_recent_corporate_action", pd.NA)
            recent_text = (
                "是"
                if pd.notna(recent) and bool(recent)
                else ("否" if pd.notna(recent) else "未知")
            )
            lines.append(
                f"| {row['symbol']} {row.get('name', '')} | "
                f"{row.get('corporate_action_status', 'N/A')} | {recent_text} | "
                f"{_date_text(row.get('last_corporate_action_date'))} |"
            )
    lines.extend(
        [
            "",
            f"- Top 50 显式停牌状态：已知 {suspension_known}/{len(dated)}；"
            f"True={suspension_true}，False={suspension_false}，Unknown={suspension_unknown}。",
            f"- Top 50 当日零成交量记录：{zero_turnover_count}；该统计不含 HSCI 基准，且 `volume=0` 不被推断为停牌。",
            f"- 当日日筛使用 `{adjustment}`；严格历史回测仍需 `NONE` 加点时公司行动账本。",
        ]
    )

    lines.extend(
        [
            "",
            "## 证据边界与下一步",
            "",
            "- 排名只证明价量与风险特征在当日股票池内相对靠前。",
            "- A 类表示优先研究，不表示估值合适、催化剂成立或可执行。",
            "- 下一步应核对公告、业绩/指引、公司行动、行业暴露及估值。",
            f"- 成本假设为每边 {config.all_in_cost_bps_per_side:.2f} bps；当日报告不声称已验证扣费后 alpha。",
        ]
    )
    for warning in (str(item) for item in evidence_warnings if str(item)):
        lines.append(f"- 数据警告：{warning}")

    if formal_publish_allowed:
        conclusion = (
            f"{date.date()} 的数据契约达到正式日筛门槛，在 `{regime}` 门控下"
            f"产生 {len(candidates)} 只 A 类研究候选。排名、ATR、扩张度、公司"
            "行动日期和未入选原因均直接来自评分表；未加入未经计算支持的市场"
            "风格叙事。空名单同样是合格结果。"
        )
    else:
        conclusion = (
            f"{date.date()} 的筛选链路已运行，但数据就绪状态为 `{readiness}`，"
            "正式 A 类候选强制为空。诊断排名只验证工程链路，不构成选股结果。"
        )
    lines.extend(["", "## 综述结论", "", conclusion, ""])

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
