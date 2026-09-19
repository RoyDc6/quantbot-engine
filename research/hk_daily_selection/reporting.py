"""Markdown reports for candidate screens and research backtests."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from .backtest import BacktestResult
from .config import StrategyConfig
from .selector import candidates_as_of


def _pct(value: object, digits: int = 1) -> str:
    if pd.isna(value):
        return "N/A"
    return f"{float(value) * 100:.{digits}f}%"


def _first_contrary_evidence(row: pd.Series) -> str:
    if row.get("extension_atr_20", 0.0) > 1.5:
        return "价格相对 20 日均线扩张过快，存在追高风险"
    if row.get("turnover_ratio_5_20", 1.0) < 0.8:
        return "近期成交额确认不足"
    if row.get("realized_vol_20", 0.0) > 0.60:
        return "20 日实现波动偏高，成本和跳空风险可能吞噬优势"
    return "当前仅有价量证据，尚未完成基本面与催化剂核验"


def write_daily_report(
    scores: pd.DataFrame,
    as_of: pd.Timestamp,
    output_path: Path | str,
    config: StrategyConfig,
    *,
    source_posture: str,
    evidence_warnings: Iterable[str] = (),
) -> Path:
    date = pd.Timestamp(as_of).normalize()
    dated = scores[scores["date"] == date].copy()
    if dated.empty:
        raise ValueError(f"no scores for {date.date()}")
    candidates = candidates_as_of(scores, date)
    diagnostics = dated[dated["priority"].eq("Diagnostic")].copy()
    diagnostics = diagnostics.sort_values(
        ["rank", "symbol"], na_position="last"
    )
    regime = str(dated["regime"].iloc[0])
    breadth = dated["breadth_above_ma20"].iloc[0]
    readiness = str(dated.get("data_readiness", pd.Series(["UNKNOWN"])).iloc[0])
    formal_publish_allowed = bool(
        dated.get("formal_publish_allowed", pd.Series([False])).iloc[0]
    )
    parent_size = int(
        dated.get("parent_universe_size", pd.Series([len(dated)])).iloc[0]
    )
    universe_size = int(
        dated.get("screen_universe_size", pd.Series([len(dated)])).iloc[0]
    )
    universe_definition = str(
        dated.get(
            "universe_definition", pd.Series(["POINT_IN_TIME_MEMBERSHIP"])
        ).iloc[0]
    )
    bar_count = int(
        dated.get("bar_coverage_count", pd.Series([dated["close"].notna().sum()])).iloc[0]
    )
    breadth_count = int(
        dated.get(
            "breadth_sample_count",
            pd.Series([(dated["close"].notna() & dated["ma_20"].notna()).sum()]),
        ).iloc[0]
    )
    bar_ratio = float(
        dated.get("bar_coverage_ratio", pd.Series([bar_count / max(universe_size, 1)])).iloc[0]
    )
    breadth_ratio = float(
        dated.get(
            "breadth_coverage_ratio",
            pd.Series([breadth_count / max(universe_size, 1)]),
        ).iloc[0]
    )
    if regime == "ON":
        candidate_limit = config.top_k
    elif regime == "CAUTION":
        candidate_limit = config.caution_top_k
    else:
        candidate_limit = 0

    lines = [
        f"# 香港市场每日优选研究候选 — {date.date()}",
        "",
        "> 研究候选，不是买入建议；不连接账户，不生成订单。",
        "",
        "## 首读结论",
        "",
        f"- 数据就绪：`{readiness}`",
        f"- 正式候选发布：{'允许' if formal_publish_allowed else '禁止'}",
        f"- 研究股票池：`{universe_definition}`（{universe_size}/{parent_size}）",
        f"- 已完成日线覆盖：{bar_count}/{universe_size}（{_pct(bar_ratio)}）",
        f"- 市场门控：`{regime}`",
        f"- 市场宽度样本：{breadth_count}/{universe_size}；"
        f"Close > MA20 比例：{_pct(breadth)}；覆盖率：{_pct(breadth_ratio)}",
        f"- A 类研究候选：{len(candidates)} 只（上限 {candidate_limit}）",
        f"- 证据状态：{source_posture}",
        "",
        "## A 类研究候选",
        "",
    ]

    if candidates.empty:
        if not formal_publish_allowed:
            lines.append(
                "数据未达到正式发布门槛，A 类候选强制为空；允许空名单。"
            )
        else:
            lines.append("当前门控或过滤条件下没有 A 类候选；允许空名单。")
    else:
        lines.extend(
            [
                "| 排名 | 代码 | 名称 | 行业 | 综合分 | 动量 | 相对强度 | 趋势质量 | 价量确认 | 风险质量 | 入场质量 | 首要反证/待核验 |",
                "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for _, row in candidates.iterrows():
            lines.append(
                "| {rank:.0f} | {symbol} | {name} | {sector} | {score:.3f} | "
                "{mom:.2f} | {rs:.2f} | {trend:.2f} | {part:.2f} | "
                "{risk:.2f} | {entry:.2f} | {reject} |".format(
                    rank=row["rank"],
                    symbol=row["symbol"],
                    name=row.get("name", ""),
                    sector=row.get("sector", "") or "未标注",
                    score=row["composite_score"],
                    mom=row["momentum_score"],
                    rs=row["relative_strength_score"],
                    trend=row["trend_quality_score"],
                    part=row["participation_score"],
                    risk=row["risk_score"],
                    entry=row["entry_quality_score"],
                    reject=_first_contrary_evidence(row),
                )
            )

    if not diagnostics.empty:
        lines.extend(
            [
                "",
                "## 诊断排名（不可发布为 A 类）",
                "",
                "> 以下仅用于验证因子链路；数据就绪前不是正式选股结果。",
                "",
                "| 诊断排名 | 代码 | 名称 | 行业 | 综合分 | 过滤状态 |",
                "|---:|---|---|---|---:|---|",
            ]
        )
        for _, row in diagnostics.head(max(config.top_k, 10)).iterrows():
            lines.append(
                f"| {row['rank']:.0f} | {row['symbol']} | "
                f"{row.get('name', '')} | {row.get('sector', '') or '未标注'} | "
                f"{row['composite_score']:.3f} | Diagnostic |"
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
            "## 候选漏斗与排除",
            "",
            f"- 股票池记录：{len(dated)}",
            f"- 可评分：{int(dated['eligible'].sum())}",
            f"- Diagnostic：{int((dated['priority'] == 'Diagnostic').sum())}",
            f"- B 类观察：{int((dated['priority'] == 'B').sum())}",
            f"- C 类屏幕标记：{int((dated['priority'] == 'C').sum())}",
            f"- Reject：{int((dated['priority'] == 'Reject').sum())}",
            f"- NotEvaluated（数据缺失）：{int((dated['priority'] == 'NotEvaluated').sum())}",
        ]
    )
    if not rejection_counts.empty:
        lines.append("")
        lines.append("主要排除原因：")
        lines.append("")
        for reason, count in rejection_counts.head(8).items():
            lines.append(f"- `{reason}`：{int(count)}")

    warnings = [str(item) for item in evidence_warnings if str(item)]
    lines.extend(
        [
            "",
            "## 证据边界与下一步",
            "",
            "- 排名只证明价量与风险特征在当日股票池内相对靠前。",
            "- A 类表示优先研究，不表示估值合适、催化剂成立或可执行。",
            "- 下一步应核对公告、业绩/指引、公司行动、行业暴露及估值。",
        ]
    )
    for warning in warnings:
        lines.append(f"- 数据警告：{warning}")

    if formal_publish_allowed:
        conclusion = (
            f"{date.date()} 的数据契约达到正式日筛门槛，在 `{regime}` "
            f"门控下产生 {len(candidates)} 只 A 类研究候选。空名单同样是"
            "合格结果，不为填满数量而补票。"
        )
    else:
        conclusion = (
            f"{date.date()} 的筛选链路已运行，但数据就绪状态为 `{readiness}`，"
            "正式 A 类候选强制为 0。诊断排名只验证工程链路，不构成选股结果。"
        )
    lines.extend(["", "## 综述结论", "", conclusion, ""])
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_backtest_report(
    result: BacktestResult,
    output_path: Path | str,
    config: StrategyConfig,
    *,
    label: str,
    source_posture: str,
) -> Path:
    metrics = result.metrics
    lines = [
        f"# 香港每日优选策略回测摘要 — {label}",
        "",
        "> 研究级结果；不代表可执行收益，不是投资建议。",
        "",
        "## 结论先行",
        "",
        f"- 证据状态：{source_posture}",
        f"- 总收益：{metrics['total_return_pct']:.2f}%",
        f"- 年化收益：{metrics['annualized_return_pct']:.2f}%",
        f"- 年化波动：{metrics['annualized_volatility_pct']:.2f}%",
        f"- Sharpe（无风险利率按 0）：{metrics['sharpe_ratio']:.2f}",
        f"- 最大回撤：{metrics['max_drawdown_pct']:.2f}%",
        f"- 完成退出：{metrics['completed_trades']}",
        f"- 平均持有：{metrics['average_holding_days']:.2f} 个交易日",
        f"- 平均总敞口：{metrics['average_exposure_pct']:.1f}%",
        f"- 模拟总成本：HK${metrics['total_cost_hkd']:,.2f}",
        "",
        "## 3/5/10 日横截面检验",
        "",
        "| 期限 | 平均 Rank IC | IC>0 比例 | 入选平均收益 | 股票池平均收益 | 超额 | 样本 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result.forward_metrics.itertuples(index=False):
        lines.append(
            f"| {row.horizon_days} | {row.mean_rank_ic:.3f} | "
            f"{row.rank_ic_positive_rate:.1%} | "
            f"{row.selected_mean_return_pct:.2f}% | "
            f"{row.universe_mean_return_pct:.2f}% | "
            f"{row.selected_excess_return_pct:.2f}% | "
            f"{row.observations} |"
        )

    lines.extend(
        [
            "",
            "## 关键假设",
            "",
            "- 信号只使用 T 日已完成日线，最早按 T+1 开盘价成交。",
            f"- 最短持有 {config.min_hold_days} 日，最迟 "
            f"{config.max_hold_days} 日退出；跌出 Rank "
            f"{config.exit_rank} 后才允许排名退出。",
            f"- 基准成本为 {config.all_in_cost_bps_per_side:.2f} bps/边。",
            f"- 单名义头寸不超过 60 日成交额中位数的 "
            f"{config.max_participation_of_median_turnover:.1%}；这是回测"
            "容量约束，不是个性化仓位建议。",
            "- 当前原型使用连续研究价格；board lot、逐笔印花税取整、"
            "真实盘口冲击和公司行动现金流需在生产前补齐。",
        ]
    )
    for warning in result.warnings:
        lines.append(f"- 数据警告：{warning}")

    lines.extend(
        [
            "",
            "## 综述结论",
            "",
            (
                "只有在点时成分、已完成日线、历史停牌、公司行动和成本数据"
                "完整，且 rolling walk-forward 样本外结果稳定时，策略才可从"
                "`PRELIMINARY` 升级。合成示例只能证明代码链路，不证明 alpha。"
            ),
            "",
        ]
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
