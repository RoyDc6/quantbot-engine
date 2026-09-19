"""Daily investability filters, regime gate, scoring, and candidate selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .contracts import (
    DataQualityResult,
    membership_at,
    validate_screen_bar_freshness,
    validate_point_in_time_coverage,
)
from .features import FEATURE_COLUMNS, compute_features


@dataclass(frozen=True)
class SelectionRun:
    scores: pd.DataFrame
    quality: DataQualityResult
    evidence_posture: str = "PRELIMINARY"
    publish_candidates: bool = False
    readiness_warnings: Tuple[str, ...] = ()


def _rank_percentile(series: pd.Series) -> pd.Series:
    if series.notna().sum() <= 1:
        return pd.Series(0.5, index=series.index, dtype=float)
    return series.rank(method="average", pct=True)


def _sector_blended_rank(
    frame: pd.DataFrame,
    raw_column: str,
    sector_blend: float,
) -> pd.Series:
    """Blend broad-universe and within-sector ranks without small-group noise."""

    global_score = _rank_percentile(frame[raw_column])
    if sector_blend <= 0.0 or "sector" not in frame:
        return global_score

    sector_score = global_score.copy()
    labels = frame["sector"].fillna("").astype(str).str.strip()
    for sector, indices in labels.groupby(labels).groups.items():
        if not sector:
            continue
        values = frame.loc[indices, raw_column]
        if values.notna().sum() >= 3:
            sector_score.loc[indices] = _rank_percentile(values)
    return (1.0 - sector_blend) * global_score + sector_blend * sector_score


def _rejection_reasons(row: pd.Series, config: StrategyConfig) -> str:
    reasons: List[str] = []
    if pd.isna(row.get("close")):
        return "missing_completed_bar"
    snapshot_suspended = row.get("snapshot_is_suspended", pd.NA)
    bar_suspended = row.get("is_suspended", pd.NA)
    suspended = (
        snapshot_suspended
        if pd.notna(snapshot_suspended)
        else bar_suspended
    )
    if pd.notna(suspended) and bool(suspended):
        reasons.append("suspended")
    recent_action = row.get("has_recent_corporate_action", pd.NA)
    adjustment = str(row.get("adjustment", "NONE") or "NONE").upper()
    if (
        pd.notna(recent_action)
        and bool(recent_action)
        and adjustment != "QFQ"
    ):
        reasons.append("recent_corporate_action")
    if row.get("volume", 0.0) <= 0 or row.get("turnover", 0.0) <= 0:
        reasons.append("no_turnover")
    if row.get("history_bars", 0) < config.min_history_bars:
        reasons.append("insufficient_history")
    if row.get("close", 0.0) < config.min_price_hkd:
        reasons.append("price_floor")
    if (
        row.get("median_turnover_60", np.nan)
        < config.min_median_turnover_60_hkd
    ):
        reasons.append("liquidity_floor")
    if row.get("active_days_20", 0) < config.min_active_days_20:
        reasons.append("inactive_sessions")
    if row.get("realized_vol_20", np.inf) > config.max_realized_vol_20:
        reasons.append("volatility_cap")
    if abs(row.get("return_1", np.inf)) > config.max_abs_return_1d:
        reasons.append("event_gap")
    if config.require_positive_trend:
        if not (
            row.get("close", -np.inf) > row.get("ma_20", np.inf)
            and row.get("ma_20", -np.inf) > row.get("ma_60", np.inf)
        ):
            reasons.append("trend_filter")
    if any(pd.isna(row.get(column)) for column in FEATURE_COLUMNS):
        reasons.append("feature_gap")
    return "|".join(dict.fromkeys(reasons))


def _regime_for_date(
    dated: pd.DataFrame,
    benchmark_row: pd.Series | None,
    config: StrategyConfig,
) -> Tuple[str, float]:
    breadth_sample = dated[dated["ma_20"].notna() & dated["close"].notna()]
    breadth = (
        float((breadth_sample["close"] > breadth_sample["ma_20"]).mean())
        if not breadth_sample.empty
        else np.nan
    )
    if benchmark_row is None or any(
        pd.isna(benchmark_row.get(field))
        for field in ["benchmark_close", "benchmark_ma_20", "benchmark_ma_60"]
    ):
        # Per WORKBUDDY_SYNC §五 (2026-07-30 修正): if the benchmark trio is
        # missing we cannot score the regime, and a missing breadth must be
        # reported as UNKNOWN rather than silently downgraded to OFF.
        return "UNKNOWN", breadth

    if not np.isfinite(breadth):
        # Breadth is the third regime pillar; if it is undefined we cannot
        # promote to ON/CAUTION, and we must not silently fall through to OFF
        # either. Lock the regime to UNKNOWN and leave the breadth sample as
        # evidence for the report.
        return "UNKNOWN", breadth

    checks = [
        benchmark_row["benchmark_close"] > benchmark_row["benchmark_ma_60"],
        benchmark_row["benchmark_ma_20"] > benchmark_row["benchmark_ma_60"],
        breadth >= config.breadth_on,
    ]
    strength = sum(bool(value) for value in checks)
    if strength == 3:
        return "ON", breadth
    if strength >= 2 and breadth >= config.breadth_caution:
        return "CAUTION", breadth
    return "OFF", breadth


def _apply_sector_and_issuer_caps(
    eligible: pd.DataFrame,
    desired: int,
    config: StrategyConfig,
) -> List[int]:
    selected, _ = _select_with_constraint_reasons(
        eligible, desired, config
    )
    return selected


def _select_with_constraint_reasons(
    eligible: pd.DataFrame,
    desired: int,
    config: StrategyConfig,
) -> Tuple[List[int], Dict[int, str]]:
    """Apply portfolio caps and retain the first binding reason per row.

    The selection result used to contain only a boolean.  That forced report
    writers to reverse-engineer why a high-ranked eligible security was not
    selected, which can be wrong once the candidate limit has already been
    filled.  This helper makes the decision trace part of the score output.
    """

    if desired <= 0 or eligible.empty:
        return [], {}
    sector_counts: Dict[str, int] = {}
    issuer_counts: Dict[str, int] = {}
    selected: List[int] = []
    reasons: Dict[int, str] = {}
    sector_data_available = (
        eligible["sector"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.len()
        .gt(0)
        .any()
    )

    ordered = eligible.sort_values(
        ["composite_score", "symbol"],
        ascending=[False, True],
    )
    for index, row in ordered.iterrows():
        if len(selected) >= desired:
            reasons[index] = "candidate_limit"
            continue
        sector = str(row.get("sector", "")).strip()
        issuer_value = row.get("issuer_id", row["symbol"])
        issuer = (
            str(row["symbol"])
            if pd.isna(issuer_value) or not str(issuer_value).strip()
            else str(issuer_value).strip()
        )
        if (
            sector_data_available
            and sector
            and sector_counts.get(sector, 0) >= config.max_per_sector
        ):
            reasons[index] = "sector_limit"
            continue
        if issuer_counts.get(issuer, 0) >= config.max_per_issuer:
            reasons[index] = "issuer_limit"
            continue
        selected.append(index)
        if sector:
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
        issuer_counts[issuer] = issuer_counts.get(issuer, 0) + 1
    return selected, reasons


def _select_screen_universe(
    members: pd.DataFrame,
    config: StrategyConfig,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Select the same-day HSCI Top-N universe using observed market value."""

    parent_size = len(members)
    market_values = pd.to_numeric(
        members.get(
            "total_market_val",
            pd.Series(np.nan, index=members.index),
        ),
        errors="coerce",
    )
    ranking_known = market_values.notna() & (market_values > 0)
    ranking_coverage = (
        float(ranking_known.mean()) if parent_size else 0.0
    )
    ranks = pd.to_numeric(
        members.get(
            "screen_universe_rank",
            pd.Series(np.nan, index=members.index),
        ),
        errors="coerce",
    )
    expected_size = min(config.screen_universe_top_n, parent_size)
    ranked = members[
        ranks.notna() & (ranks >= 1) & (ranks <= expected_size)
    ].copy()
    if len(ranked) == expected_size and expected_size > 0:
        ranked["screen_universe_rank"] = pd.to_numeric(
            ranked["screen_universe_rank"], errors="coerce"
        )
        ranked = ranked.sort_values(
            ["screen_universe_rank", "symbol"]
        )
        universe = ranked
        definition = (
            f"HSCI_TOP_{expected_size}_BY_TOTAL_MARKET_VAL_ASOF"
        )
    else:
        # Do not guess a Top-N universe from row order.  Keeping all members
        # makes the low ranking coverage visible and guarantees fail-close.
        universe = members.copy()
        definition = "HSCI_ALL_FALLBACK_UNRANKED"

    return universe, {
        "parent_universe_size": parent_size,
        "screen_universe_size": len(universe),
        "screen_universe_expected_size": expected_size,
        "universe_definition": definition,
        "universe_ranking_coverage_ratio": ranking_coverage,
    }


def _screen_readiness(
    dated: pd.DataFrame,
    universe_metadata: Dict[str, object],
    config: StrategyConfig,
) -> Tuple[bool, Dict[str, object], Tuple[str, ...]]:
    """Assess whether diagnostic ranks may be published as formal A class."""

    size = len(dated)
    denominator = max(size, 1)
    bar_mask = dated["close"].notna()
    breadth_mask = dated["close"].notna() & dated["ma_20"].notna()
    sector_values = dated["sector"].fillna("").astype(str).str.strip()
    sector_sources = dated.get(
        "sector_source", pd.Series("", index=dated.index)
    ).fillna("").astype(str).str.strip()
    sector_mask = sector_values.ne("") & sector_sources.ne("")
    suspension_values = dated.get(
        "snapshot_is_suspended",
        pd.Series(pd.NA, index=dated.index, dtype="boolean"),
    )
    suspension_times = dated.get(
        "market_snapshot_observed_at_hkt",
        pd.Series(pd.NaT, index=dated.index),
    )
    suspension_sources = dated.get(
        "suspension_source", pd.Series("", index=dated.index)
    ).fillna("").astype(str).str.strip()
    suspension_mask = (
        suspension_values.notna()
        & suspension_times.notna()
        & suspension_sources.ne("")
    )
    action_status = dated.get(
        "corporate_action_status", pd.Series("", index=dated.index)
    ).fillna("").astype(str).str.upper()
    action_times = dated.get(
        "corporate_action_observed_at_hkt",
        pd.Series(pd.NaT, index=dated.index),
    )
    action_mask = action_status.isin(["CLEAR", "RECENT_ACTION"]) & (
        action_times.notna()
    )

    ratios = {
        "bar_coverage_ratio": float(bar_mask.sum() / denominator),
        "breadth_coverage_ratio": float(
            breadth_mask.sum() / denominator
        ),
        "sector_coverage_ratio": float(sector_mask.sum() / denominator),
        "suspension_coverage_ratio": float(
            suspension_mask.sum() / denominator
        ),
        "corporate_action_coverage_ratio": float(
            action_mask.sum() / denominator
        ),
    }
    counts: Dict[str, object] = {
        "bar_coverage_count": int(bar_mask.sum()),
        "breadth_sample_count": int(breadth_mask.sum()),
        "sector_coverage_count": int(sector_mask.sum()),
        "suspension_coverage_count": int(suspension_mask.sum()),
        "corporate_action_coverage_count": int(action_mask.sum()),
    }
    metrics: Dict[str, object] = {
        **universe_metadata,
        **counts,
        **ratios,
    }

    requirements = [
        (
            "universe ranking",
            float(
                universe_metadata.get(
                    "universe_ranking_coverage_ratio", 0.0
                )
            ),
            config.min_screen_universe_ranking_coverage,
        ),
        (
            "completed bars",
            ratios["bar_coverage_ratio"],
            config.min_screen_bar_coverage_ratio,
        ),
        (
            "breadth sample",
            ratios["breadth_coverage_ratio"],
            config.min_screen_breadth_coverage_ratio,
        ),
        (
            "sector evidence",
            ratios["sector_coverage_ratio"],
            config.min_screen_sector_coverage_ratio,
        ),
        (
            "suspension evidence",
            ratios["suspension_coverage_ratio"],
            config.min_screen_suspension_coverage_ratio,
        ),
        (
            "corporate-action evidence",
            ratios["corporate_action_coverage_ratio"],
            config.min_screen_corporate_action_coverage_ratio,
        ),
    ]
    warnings = []
    for label, actual, minimum in requirements:
        if actual + 1e-12 < minimum:
            warnings.append(
                f"DATA_READINESS: {label} coverage {actual:.2%} is below "
                f"required {minimum:.2%}"
            )
    ready = size > 0 and not warnings
    metrics["data_readiness"] = "READY" if ready else "PARTIAL"
    metrics["formal_publish_allowed"] = ready
    return ready, metrics, tuple(warnings)


def _score_date(
    dated: pd.DataFrame,
    regime: str,
    breadth: float,
    config: StrategyConfig,
    *,
    publish_candidates: bool = True,
    run_metadata: Dict[str, object] | None = None,
) -> pd.DataFrame:
    result = dated.copy()
    result["rejection_reason"] = result.apply(
        _rejection_reasons, axis=1, config=config
    )
    result["eligible"] = result["rejection_reason"].eq("")
    result["regime"] = regime
    result["breadth_above_ma20"] = breadth
    result["selected"] = False
    result["priority"] = np.where(
        result["rejection_reason"].eq("missing_completed_bar"),
        "NotEvaluated",
        "Reject",
    )
    result["rank"] = np.nan
    result["composite_score"] = np.nan
    result["selection_reason"] = np.where(
        result["rejection_reason"].eq("missing_completed_bar"),
        "missing_completed_bar",
        "hard_filter:" + result["rejection_reason"].astype(str),
    )
    for key, value in (run_metadata or {}).items():
        result[key] = value

    eligible = result[result["eligible"]].copy()
    if eligible.empty:
        return result

    eligible["momentum_raw"] = (
        eligible["mom_20_5"] + eligible["mom_60_5"]
    ) / 2.0
    eligible["trend_quality_raw"] = (
        eligible["trend_strength"] + eligible["trend_efficiency_20"]
    ) / 2.0
    eligible["participation_raw"] = (
        eligible["turnover_ratio_5_20"] + eligible["up_turnover_share_20"]
    ) / 2.0
    eligible["risk_raw"] = (
        -eligible["realized_vol_20"] + eligible["drawdown_20"]
    )
    eligible["research_capacity_hkd"] = (
        eligible["median_turnover_60"]
        * config.max_participation_of_median_turnover
    )

    component_map = {
        "momentum": "momentum_raw",
        "relative_strength": "relative_strength_20",
        "trend_quality": "trend_quality_raw",
        "participation": "participation_raw",
        "risk": "risk_raw",
        "entry_quality": "entry_quality",
    }
    for component, raw_column in component_map.items():
        eligible[f"{component}_score"] = _sector_blended_rank(
            eligible,
            raw_column,
            config.sector_neutral_blend,
        )

    eligible["composite_score"] = 0.0
    for component, weight in config.factor_weights.items():
        eligible["composite_score"] += (
            weight * eligible[f"{component}_score"]
        )
    rank_order = eligible.sort_values(
        ["composite_score", "symbol"], ascending=[False, True]
    ).index
    eligible["rank"] = np.nan
    eligible.loc[rank_order, "rank"] = np.arange(
        1, len(rank_order) + 1, dtype=float
    )

    if not publish_candidates:
        eligible["selected"] = False
        eligible["priority"] = "Diagnostic"
        eligible["selection_reason"] = "data_readiness"
        update_columns = [
            "eligible",
            "selected",
            "priority",
            "rank",
            "composite_score",
            "momentum_score",
            "relative_strength_score",
            "trend_quality_score",
            "participation_score",
            "risk_score",
            "entry_quality_score",
            "research_capacity_hkd",
            "selection_reason",
        ]
        for column in update_columns:
            if column not in result:
                result[column] = np.nan
            result.loc[eligible.index, column] = eligible[column]
        return result

    if regime == "ON":
        desired = config.top_k
    elif regime == "CAUTION":
        desired = config.caution_top_k
    elif regime == "UNKNOWN" and config.allow_unknown_regime:
        desired = config.caution_top_k
    else:
        desired = 0

    if desired > 0:
        selected_indices, constraint_reasons = (
            _select_with_constraint_reasons(eligible, desired, config)
        )
        eligible["selection_reason"] = eligible.index.to_series().map(
            constraint_reasons
        ).astype("object")
    else:
        selected_indices = []
        eligible["selection_reason"] = "market_gate"
    eligible.loc[selected_indices, "selected"] = True
    eligible.loc[selected_indices, "priority"] = "A"
    eligible.loc[selected_indices, "selection_reason"] = (
        "selected_under_constraints"
    )
    watchlist = (
        ~eligible["selected"]
        & (eligible["rank"] <= max(config.exit_rank, config.top_k * 2))
    )
    eligible.loc[watchlist, "priority"] = "B"
    eligible.loc[
        ~(eligible["selected"] | watchlist), "priority"
    ] = "C"

    update_columns = [
        "eligible",
        "selected",
        "priority",
        "rank",
        "composite_score",
        "momentum_score",
        "relative_strength_score",
        "trend_quality_score",
        "participation_score",
        "risk_score",
        "entry_quality_score",
        "research_capacity_hkd",
        "selection_reason",
    ]
    for column in update_columns:
        if column not in result:
            result[column] = np.nan
        result.loc[eligible.index, column] = eligible[column]
    return result


def run_selection(
    bars: pd.DataFrame,
    membership: pd.DataFrame,
    config: StrategyConfig | None = None,
    *,
    allow_survivorship_bias: bool = False,
    mode: str = "backtest",
    session_date: pd.Timestamp | None = None,
) -> SelectionRun:
    """Calculate candidate scores under screen or backtest data contracts.

    Parameters
    ----------
    mode:
        ``"screen"`` validates fetch provenance and PIT coverage for only
        ``session_date`` (or the latest date in ``bars``), truncates all
        feature inputs at that date, and emits only that date. ``"backtest"``
        validates every trading date implied by ``bars``.
    session_date:
        Optional screen anchor; the latest benchmark date is used when absent.
        Ignored in backtest mode.
    """

    cfg = config or StrategyConfig()
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"screen", "backtest"}:
        raise ValueError("mode must be 'screen' or 'backtest'")

    trading_dates = sorted(
        bars.loc[bars["symbol"] == cfg.benchmark_symbol, "date"].unique()
    )
    if not trading_dates:
        trading_dates = sorted(bars["date"].unique())
    if not trading_dates:
        raise ValueError("bars contain no trading dates")

    if normalized_mode == "screen":
        anchor = (
            pd.Timestamp(session_date).normalize()
            if session_date is not None
            else pd.Timestamp(trading_dates[-1])
        )
        analysis_bars = bars[bars["date"] <= anchor].copy()
        output_dates = [anchor]
        freshness = validate_screen_bar_freshness(analysis_bars, anchor)
        coverage = validate_point_in_time_coverage(
            membership,
            [anchor],
            allow_survivorship_bias=allow_survivorship_bias,
            mode="screen",
        )
        quality = DataQualityResult(
            freshness.ok and coverage.ok,
            freshness.errors + coverage.errors,
            freshness.warnings + coverage.warnings,
        )
    else:
        analysis_bars = bars
        output_dates = trading_dates
        coverage = validate_point_in_time_coverage(
            membership,
            trading_dates,
            allow_survivorship_bias=allow_survivorship_bias,
            mode="backtest",
        )
        quality = coverage
    quality.require()

    features = compute_features(
        analysis_bars,
        cfg.benchmark_symbol,
        annualization_days=cfg.annualization_days,
    )
    stock_features = features[features["symbol"] != cfg.benchmark_symbol]
    outputs: List[pd.DataFrame] = []
    readiness_warnings: List[str] = []
    publish_flags: List[bool] = []

    for date in pd.DatetimeIndex(output_dates):
        full_members = membership_at(membership, date)
        full_members = full_members[
            full_members["security_type"].isin(cfg.allowed_security_types)
        ].copy()
        if normalized_mode == "screen":
            members, universe_metadata = _select_screen_universe(
                full_members, cfg
            )
        else:
            members = full_members
            universe_metadata = {
                "parent_universe_size": len(full_members),
                "screen_universe_size": len(full_members),
                "screen_universe_expected_size": len(full_members),
                "universe_definition": "POINT_IN_TIME_MEMBERSHIP",
                "universe_ranking_coverage_ratio": 1.0,
            }
        if members.empty:
            continue
        dated_features = stock_features[
            stock_features["date"] == date
        ].rename(columns={"suspension_source": "bar_suspension_source"})
        dated = members.merge(dated_features, on="symbol", how="left")
        dated["date"] = date

        benchmark_rows = features[
            (features["date"] == date)
            & (features["symbol"] == cfg.benchmark_symbol)
        ]
        benchmark_row = (
            benchmark_rows.iloc[0] if not benchmark_rows.empty else None
        )
        regime, breadth = _regime_for_date(dated, benchmark_row, cfg)
        if normalized_mode == "screen":
            publish_ready, run_metadata, warnings = _screen_readiness(
                dated, universe_metadata, cfg
            )
            readiness_warnings.extend(warnings)
            publish_flags.append(publish_ready)
            if (
                float(run_metadata["breadth_coverage_ratio"])
                + 1e-12
                < cfg.min_screen_breadth_coverage_ratio
            ):
                regime = "UNKNOWN"
        else:
            publish_ready = True
            run_metadata = universe_metadata
            run_metadata["data_readiness"] = "BACKTEST"
            run_metadata["formal_publish_allowed"] = True
            publish_flags.append(True)
        outputs.append(
            _score_date(
                dated,
                regime,
                breadth,
                cfg,
                publish_candidates=publish_ready,
                run_metadata=run_metadata,
            )
        )

    scores = (
        pd.concat(outputs, ignore_index=True)
        if outputs
        else pd.DataFrame()
    )
    if not scores.empty:
        scores = scores.sort_values(
            ["date", "selected", "rank", "symbol"],
            ascending=[True, False, True, True],
        ).reset_index(drop=True)
    unique_readiness_warnings = tuple(dict.fromkeys(readiness_warnings))
    quality = DataQualityResult(
        quality.ok,
        quality.errors,
        tuple(dict.fromkeys(quality.warnings + unique_readiness_warnings)),
    )
    publish_candidates = bool(publish_flags) and all(publish_flags)
    if normalized_mode == "screen":
        if allow_survivorship_bias:
            evidence_posture = "PRELIMINARY / SURVIVORSHIP_BIASED"
            publish_candidates = False
            if not scores.empty:
                scores["selected"] = False
                scores.loc[scores["eligible"], "priority"] = "Diagnostic"
                scores.loc[
                    scores["eligible"], "selection_reason"
                ] = "survivorship_bias_not_publishable"
                scores["formal_publish_allowed"] = False
        elif publish_candidates:
            evidence_posture = "RESEARCH_GRADE_INPUT_CONTRACT"
        else:
            evidence_posture = "PARTIAL / DATA_READINESS"
    else:
        evidence_posture = "PRELIMINARY / POINT_IN_TIME_CONTRACT_ENFORCED"
    return SelectionRun(
        scores=scores,
        quality=quality,
        evidence_posture=evidence_posture,
        publish_candidates=publish_candidates,
        readiness_warnings=unique_readiness_warnings,
    )


def candidates_as_of(scores: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    date = pd.Timestamp(as_of).normalize()
    selected = scores[(scores["date"] == date) & scores["selected"]].copy()
    return selected.sort_values(["rank", "symbol"]).reset_index(drop=True)
