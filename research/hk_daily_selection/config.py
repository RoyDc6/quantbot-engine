"""Configuration for the Hong Kong daily-selection research strategy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class StrategyConfig:
    """Deterministic baseline parameters.

    The weights are research priors, not fitted optima. Any change must be
    evaluated out of sample before it is promoted beyond screen-grade use.
    """

    # Candidate and holding rules
    top_k: int = 5
    caution_top_k: int = 3
    exit_rank: int = 15
    min_hold_days: int = 3
    max_hold_days: int = 10
    max_per_sector: int = 2
    max_per_issuer: int = 1
    benchmark_symbol: str = "800701.HK"
    allowed_security_types: Tuple[str, ...] = ("STOCK",)

    # Investability filters
    min_history_bars: int = 120
    min_price_hkd: float = 1.0
    min_median_turnover_60_hkd: float = 30_000_000.0
    min_active_days_20: int = 18
    max_realized_vol_20: float = 0.80
    max_abs_return_1d: float = 0.25
    require_positive_trend: bool = True

    # Daily-screen universe and evidence-readiness gates.  The live screen is
    # intentionally defined as the largest HSCI companies by same-day market
    # value so that the bounded Futu cache has a reproducible denominator.
    # These gates control whether diagnostic ranks may be promoted to formal
    # A-class research candidates; they never force a non-empty list.
    screen_universe_top_n: int = 50
    min_screen_universe_ranking_coverage: float = 0.95
    min_screen_bar_coverage_ratio: float = 0.90
    min_screen_breadth_coverage_ratio: float = 0.80
    min_screen_sector_coverage_ratio: float = 0.90
    min_screen_suspension_coverage_ratio: float = 1.00
    min_screen_corporate_action_coverage_ratio: float = 1.00
    recent_corporate_action_lookback_days: int = 90

    # Factor weights. Components are cross-sectional percentile scores.
    weight_momentum: float = 0.20
    weight_relative_strength: float = 0.15
    weight_trend_quality: float = 0.15
    weight_participation: float = 0.20
    weight_risk: float = 0.15
    weight_entry_quality: float = 0.15
    sector_neutral_blend: float = 0.30

    # Market-regime thresholds
    breadth_on: float = 0.55
    breadth_caution: float = 0.45
    allow_unknown_regime: bool = False

    # Screen/backtest execution assumptions
    initial_capital_hkd: float = 1_000_000.0
    max_participation_of_median_turnover: float = 0.01
    statutory_levies_bps_per_side: float = 1.27
    stamp_duty_bps_per_side: float = 10.0
    broker_commission_bps_per_side: float = 3.0
    slippage_bps_per_side: float = 10.0
    annualization_days: int = 252

    def __post_init__(self) -> None:
        normalized_types = tuple(
            str(item).strip().upper()
            for item in self.allowed_security_types
            if str(item).strip()
        )
        object.__setattr__(self, "allowed_security_types", normalized_types)
        if self.top_k <= 0:
            raise ValueError("top_k must be positive")
        if not 0 <= self.caution_top_k <= self.top_k:
            raise ValueError("caution_top_k must be between 0 and top_k")
        if not 1 <= self.min_hold_days <= self.max_hold_days:
            raise ValueError("holding days must satisfy 1 <= min <= max")
        if self.exit_rank < self.top_k:
            raise ValueError("exit_rank must be at least top_k")
        if self.max_per_sector <= 0 or self.max_per_issuer <= 0:
            raise ValueError("sector and issuer caps must be positive")
        if self.min_history_bars < 60:
            raise ValueError("min_history_bars must be at least 60")
        if not self.allowed_security_types:
            raise ValueError("allowed_security_types cannot be empty")
        if (
            self.min_price_hkd <= 0.0
            or self.min_median_turnover_60_hkd <= 0.0
        ):
            raise ValueError("price and turnover floors must be positive")
        if not 1 <= self.min_active_days_20 <= 20:
            raise ValueError("min_active_days_20 must be between 1 and 20")
        if self.screen_universe_top_n <= 0:
            raise ValueError("screen_universe_top_n must be positive")
        readiness_ratios = {
            "min_screen_universe_ranking_coverage": (
                self.min_screen_universe_ranking_coverage
            ),
            "min_screen_bar_coverage_ratio": self.min_screen_bar_coverage_ratio,
            "min_screen_breadth_coverage_ratio": (
                self.min_screen_breadth_coverage_ratio
            ),
            "min_screen_sector_coverage_ratio": (
                self.min_screen_sector_coverage_ratio
            ),
            "min_screen_suspension_coverage_ratio": (
                self.min_screen_suspension_coverage_ratio
            ),
            "min_screen_corporate_action_coverage_ratio": (
                self.min_screen_corporate_action_coverage_ratio
            ),
        }
        for name, value in readiness_ratios.items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.recent_corporate_action_lookback_days <= 0:
            raise ValueError(
                "recent_corporate_action_lookback_days must be positive"
            )
        if not 0.0 <= self.sector_neutral_blend <= 1.0:
            raise ValueError("sector_neutral_blend must be between 0 and 1")
        if not 0.0 < self.max_participation_of_median_turnover <= 0.05:
            raise ValueError(
                "max_participation_of_median_turnover must be in (0, 0.05]"
            )
        weights = self.factor_weights
        if any(weight < 0.0 for weight in weights.values()):
            raise ValueError("factor weights cannot be negative")
        if abs(sum(weights.values()) - 1.0) > 1e-9:
            raise ValueError("factor weights must sum to 1.0")
        if not 0.0 <= self.breadth_caution <= self.breadth_on <= 1.0:
            raise ValueError(
                "breadth thresholds must satisfy 0 <= caution <= on <= 1"
            )
        costs = (
            self.statutory_levies_bps_per_side,
            self.stamp_duty_bps_per_side,
            self.broker_commission_bps_per_side,
            self.slippage_bps_per_side,
        )
        if any(cost < 0.0 for cost in costs):
            raise ValueError("cost assumptions cannot be negative")
        if self.annualization_days <= 0:
            raise ValueError("annualization_days must be positive")

    @property
    def factor_weights(self) -> Dict[str, float]:
        return {
            "momentum": self.weight_momentum,
            "relative_strength": self.weight_relative_strength,
            "trend_quality": self.weight_trend_quality,
            "participation": self.weight_participation,
            "risk": self.weight_risk,
            "entry_quality": self.weight_entry_quality,
        }

    @property
    def all_in_cost_bps_per_side(self) -> float:
        return (
            self.statutory_levies_bps_per_side
            + self.stamp_duty_bps_per_side
            + self.broker_commission_bps_per_side
            + self.slippage_bps_per_side
        )

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)
