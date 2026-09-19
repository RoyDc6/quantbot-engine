"""Independent Northstar-D1 decision model."""

from __future__ import annotations

import copy
from typing import Dict, Iterable, List

import pandas as pd

from .factors import (
    calc_dual_trend,
    calc_td_sequence,
    calc_structure_layer,
    get_structure_state,
    get_td_state,
    get_trend_state,
)
from .config import NorthstarD1Config


class NorthstarD1Model:
    """Pure research model with no controller, account or execution access."""

    REQUIRED_COLUMNS = {"open", "high", "low", "close", "volume"}

    def __init__(self, config: NorthstarD1Config | None = None):
        self.config = config or NorthstarD1Config()

    def analyze(
        self,
        df: pd.DataFrame,
        *,
        symbol: str,
        market: str,
        name: str = "",
        bar_confirmed: bool = True,
    ) -> Dict:
        """Analyze one symbol using only completed daily bars."""

        self._validate_input(df)
        cfg = self.config
        trend_frame = calc_dual_trend(df, cfg.short_period, cfg.long_period)
        structure_frame = calc_structure_layer(
            df,
            cfg.macd_fast,
            cfg.macd_slow,
            cfg.macd_signal,
            cfg.structure_threshold,
        )
        td_frame = calc_td_sequence(df["close"], cfg.td_period)

        trend = get_trend_state(trend_frame)
        structure = get_structure_state(structure_frame)
        td = get_td_state(td_frame)
        candidate = self.decide(trend, structure, td)

        signal_asof = self._signal_asof(df)
        market_data = dict(df.attrs.get("market_data") or {})
        data_fresh = (
            df.attrs.get("source") == "FUTU_OPEND_LIVE"
            and df.attrs.get("freshness_status") == "FRESH"
            and df.attrs.get("cache_used") is False
            and df.attrs.get("fallback_used") is False
            and market_data.get("request_id") == df.attrs.get("request_id")
        )
        signal_eligible = bool(bar_confirmed and data_fresh)
        action = candidate["action"] if signal_eligible else "HOLD"
        status = "CONFIRMED" if signal_eligible else "BLOCKED"
        status_codes: List[str] = []
        if not bar_confirmed:
            status_codes.append("INCOMPLETE_DAILY_BAR_BLOCKED")
        if not data_fresh:
            status_codes.append("FUTU_LIVE_FRESHNESS_BLOCKED")

        decision_audit = copy.deepcopy(candidate["decision_audit"])
        decision_audit["decision"].update(
            {
                "candidate_action": candidate["action"],
                "emitted_action": action,
                "signal_eligible": signal_eligible,
                "emission_status": (
                    "EMITTED" if signal_eligible else "BLOCKED_TO_HOLD"
                ),
                "eligibility_override_codes": list(status_codes),
            }
        )

        fraction = float(candidate["signal_fraction"]) if signal_eligible else 0.0
        risk_capped = (
            min(fraction, cfg.max_single_position_pct)
            if action == "BUY"
            else fraction
        )

        return {
            "schema_version": "2.1",
            "model_id": "NORTHSTAR_D1",
            "model_version": "1.0.0",
            "mode": "RESEARCH_ONLY",
            "symbol": symbol,
            "name": name or symbol,
            "market": market.upper(),
            "signal_asof": signal_asof,
            "bar_confirmed": bool(bar_confirmed),
            "data_fresh": data_fresh,
            "signal_eligible": signal_eligible,
            "status": status,
            "close": round(float(trend["close"]), 4),
            "action": action,
            "signal_fraction": round(fraction, 4),
            "raw_signal_fraction": round(float(candidate["signal_fraction"]), 4),
            "risk_capped_fraction": round(float(risk_capped), 4),
            "fraction_semantics": (
                "desired_long_exposure" if action == "BUY" else
                "fraction_of_existing_position_to_reduce" if action == "SELL" else
                "no_position_change"
            ),
            "confidence": round(float(candidate["confidence"]), 4),
            "confidence_semantics": "deterministic_evidence_strength_not_probability",
            "signal_type": candidate["signal_type"],
            "reason_codes": list(candidate["reason_codes"]),
            "reason": candidate["reason"],
            "decision_audit": decision_audit,
            "status_codes": status_codes,
            "market_data": market_data,
            "model_contract": {
                "frequency": "D1",
                "decision_profile": "NORTHSTAR_D1_CORE",
                "input_finality": "completed_session_only",
            },
            "execution": {
                "enabled": False,
                "account_access": False,
                "order_created": False,
            },
        }

    def decide(self, trend: Dict, structure: Dict, td: Dict) -> Dict:
        """Apply trend first, structure second and TD only as confidence."""

        cfg = self.config
        short_up = bool(trend.get("cross_short_up"))
        long_up = bool(trend.get("cross_long_up"))
        short_down = bool(trend.get("cross_short_down"))
        long_down = bool(trend.get("cross_long_down"))
        has_up = short_up or long_up
        has_down = short_down or long_down

        if has_up and has_down:
            decision = self._decision(
                "HOLD", 0.0, 0.0, "CONFLICTING_TREND_CROSS",
                ["CONFLICTING_TREND_CROSS"],
                "多空趋势交叉同时出现，安全观望",
            )
            decision["decision_audit"] = self._build_decision_audit(
                trend=trend,
                structure=structure,
                td=td,
                action="HOLD",
                signal_type=decision["signal_type"],
                reason_codes=decision["reason_codes"],
                reason=decision["reason"],
                primary_fraction=0.0,
                primary_confidence=0.0,
                structure_fraction=0.0,
                structure_confidence=0.0,
                final_confidence=0.0,
            )
            return decision

        action = "HOLD"
        fraction = 0.0
        confidence = 0.0
        signal_type = "NO_EVENT"
        reason_codes: List[str] = []
        reason = "无新的趋势或结构事件"

        if short_up and long_up:
            action = "BUY"
            fraction = cfg.double_cross_fraction
            confidence = 0.86
            signal_type = "DOUBLE_BREAKOUT"
            reason_codes.append("TREND_DOUBLE_UP")
            reason = "短顶与长顶同步突破，趋势层直接做多"
        elif long_up:
            action = "BUY"
            fraction = cfg.long_cross_fraction
            confidence = 0.78
            signal_type = "LONG_BREAKOUT"
            reason_codes.append("TREND_LONG_UP")
            reason = "突破长顶，趋势层直接做多"
        elif short_up:
            action = "BUY"
            fraction = cfg.short_cross_fraction
            confidence = 0.68
            signal_type = "SHORT_BREAKOUT"
            reason_codes.append("TREND_SHORT_UP")
            reason = "突破短顶，趋势层建立基础多头信号"
        elif short_down and long_down:
            action = "SELL"
            fraction = cfg.double_breakdown_fraction
            confidence = 0.90
            signal_type = "DOUBLE_BREAKDOWN"
            reason_codes.append("TREND_DOUBLE_DOWN")
            reason = "短底与长底同步跌破，趋势层要求退出"
        elif long_down:
            action = "SELL"
            fraction = cfg.long_cross_fraction
            confidence = 0.78
            signal_type = "LONG_BREAKDOWN"
            reason_codes.append("TREND_LONG_DOWN")
            reason = "跌破长底，趋势层要求减仓"
        elif short_down:
            action = "SELL"
            fraction = cfg.short_cross_fraction
            confidence = 0.68
            signal_type = "SHORT_BREAKDOWN"
            reason_codes.append("TREND_SHORT_DOWN")
            reason = "跌破短底，趋势层要求减仓"
        else:
            action, fraction, confidence, signal_type, reason_codes, reason = (
                self._structure_only_decision(trend, structure)
            )

        primary_fraction = fraction
        primary_confidence = confidence
        fraction, confidence, modifier_codes = self._apply_structure_modifiers(
            action,
            fraction,
            confidence,
            structure,
            signal_type.startswith(("SHORT_", "LONG_", "DOUBLE_")),
        )
        reason_codes.extend(modifier_codes)
        structure_fraction = fraction
        structure_confidence = confidence
        confidence, td_codes = self._apply_td_confidence(action, confidence, td)
        reason_codes.extend(td_codes)

        decision = self._decision(
            action,
            fraction,
            confidence,
            signal_type,
            reason_codes,
            reason,
        )
        decision["decision_audit"] = self._build_decision_audit(
            trend=trend,
            structure=structure,
            td=td,
            action=action,
            signal_type=signal_type,
            reason_codes=reason_codes,
            reason=reason,
            primary_fraction=primary_fraction,
            primary_confidence=primary_confidence,
            structure_fraction=structure_fraction,
            structure_confidence=structure_confidence,
            final_confidence=confidence,
        )
        return decision

    def _structure_only_decision(self, trend: Dict, structure: Dict):
        market = trend.get("market", "SIDEWAYS")
        bottom_new = bool(structure.get("bottom_structure_new"))
        top_new = bool(structure.get("top_structure_new"))

        if market == "UP" and bottom_new:
            return (
                "BUY",
                self.config.aligned_structure_fraction,
                0.62,
                "UPTREND_BOTTOM_STRUCTURE",
                ["STRUCTURE_BOTTOM_ALIGNED"],
                "上升趋势中的底部结构，作为顺势加仓时点",
            )
        if market == "UP" and top_new:
            return (
                "SELL",
                self.config.aligned_structure_fraction,
                0.64,
                "UPTREND_TOP_STRUCTURE",
                ["STRUCTURE_TOP_TRIM"],
                "上升趋势中的顶部结构，只做修边减仓",
            )
        if market == "DOWN" and top_new:
            return (
                "SELL",
                self.config.aligned_structure_fraction,
                0.68,
                "DOWNTREND_TOP_STRUCTURE",
                ["STRUCTURE_TOP_ALIGNED"],
                "下降趋势中的顶部结构，顺势降低多头暴露",
            )
        if market == "DOWN" and bottom_new:
            return (
                "HOLD",
                0.0,
                0.0,
                "DOWNTREND_BOTTOM_WATCH",
                ["COUNTERTREND_STRUCTURE_WATCH_ONLY"],
                "下降趋势中的底部结构仅观察，等待趋势反转",
            )
        return "HOLD", 0.0, 0.0, "NO_EVENT", [], "无新的趋势或结构事件"

    def _apply_structure_modifiers(
        self,
        action: str,
        fraction: float,
        confidence: float,
        structure: Dict,
        is_trend_event: bool,
    ):
        if not is_trend_event or action == "HOLD":
            return fraction, confidence, []

        codes: List[str] = []
        bottom = bool(structure.get("bottom_structure"))
        top = bool(structure.get("top_structure"))
        trim = self.config.opposing_structure_trim

        if action == "BUY" and bottom:
            fraction = min(1.0, fraction + trim)
            confidence += 0.05
            codes.append("STRUCTURE_ALIGNED_ADD")
        elif action == "BUY" and top:
            fraction = max(0.0, fraction - trim)
            confidence -= 0.10
            codes.append("STRUCTURE_OPPOSED_TRIM")
        elif action == "SELL" and top:
            fraction = min(1.0, fraction + trim)
            confidence += 0.05
            codes.append("STRUCTURE_ALIGNED_ADD")
        elif action == "SELL" and bottom:
            fraction = max(0.0, fraction - trim)
            confidence -= 0.10
            codes.append("STRUCTURE_OPPOSED_TRIM")
        return fraction, confidence, codes

    @staticmethod
    def _apply_td_confidence(action: str, confidence: float, td: Dict):
        if action == "HOLD" or not (td.get("td_near") or td.get("td_reached")):
            return confidence, []

        aligned = (
            (action == "BUY" and td.get("is_buy_seq"))
            or (action == "SELL" and td.get("is_sell_seq"))
        )
        delta = 0.10 if td.get("td_reached") else 0.05
        if aligned:
            return min(0.95, confidence + delta), ["TD_ALIGNED_CONFIDENCE_UP"]
        return max(0.40, confidence - delta), ["TD_OPPOSED_CONFIDENCE_DOWN"]

    @staticmethod
    def _structure_side_audit(structure: Dict, side: str) -> Dict:
        plateau = bool(structure.get(f"{side}_plateau"))
        plateau_new = bool(structure.get(f"{side}_plateau_new"))
        plateau_disappeared = bool(
            structure.get(f"{side}_plateau_disappeared")
        )
        confirmed = bool(structure.get(f"{side}_structure"))
        confirmed_new = bool(structure.get(f"{side}_structure_new"))
        if confirmed_new:
            stage = "NEW_CONFIRMED"
        elif confirmed:
            stage = "CONFIRMED"
        elif plateau_new:
            stage = "NEW_PLATEAU"
        elif plateau:
            stage = "PLATEAU"
        elif plateau_disappeared:
            stage = "PLATEAU_DISAPPEARED"
        else:
            stage = "NONE"
        return {
            "stage": stage,
            "plateau": plateau,
            "plateau_new": plateau_new,
            "plateau_disappeared": plateau_disappeared,
            "confirmed": confirmed,
            "confirmed_new": confirmed_new,
            "persistence_days": int(structure.get(f"{side}_plateau_days") or 0),
        }

    @classmethod
    def _build_decision_audit(
        cls,
        *,
        trend: Dict,
        structure: Dict,
        td: Dict,
        action: str,
        signal_type: str,
        reason_codes: Iterable[str],
        reason: str,
        primary_fraction: float,
        primary_confidence: float,
        structure_fraction: float,
        structure_confidence: float,
        final_confidence: float,
    ) -> Dict:
        codes = list(reason_codes)
        bottom = cls._structure_side_audit(structure, "bottom")
        top = cls._structure_side_audit(structure, "top")

        if "STRUCTURE_ALIGNED_ADD" in codes:
            structure_effect = "ALIGNED_ADD"
        elif "STRUCTURE_OPPOSED_TRIM" in codes:
            structure_effect = "OPPOSED_TRIM"
        elif signal_type in {
            "UPTREND_BOTTOM_STRUCTURE",
            "UPTREND_TOP_STRUCTURE",
            "DOWNTREND_TOP_STRUCTURE",
        }:
            structure_effect = "PRIMARY_TRIGGER"
        elif signal_type == "DOWNTREND_BOTTOM_WATCH":
            structure_effect = "WATCH_ONLY"
        elif bottom["stage"] != "NONE" or top["stage"] != "NONE":
            structure_effect = "OBSERVED_NO_ADJUSTMENT"
        else:
            structure_effect = "NONE"

        count = int(td.get("td_count") or 0)
        if td.get("td_reached"):
            sequence_stage = "REACHED"
        elif td.get("td_near"):
            sequence_stage = "NEAR"
        elif count:
            sequence_stage = "BUILDING"
        else:
            sequence_stage = "NONE"
        sequence_direction = (
            "BUY" if td.get("is_buy_seq") else
            "SELL" if td.get("is_sell_seq") else
            "NONE"
        )
        sequence_active = bool(td.get("td_near") or td.get("td_reached"))
        if not sequence_active:
            sequence_alignment = "NOT_ACTIVE"
        elif action == "HOLD":
            sequence_alignment = "NOT_APPLICABLE"
        elif (
            (action == "BUY" and td.get("is_buy_seq"))
            or (action == "SELL" and td.get("is_sell_seq"))
        ):
            sequence_alignment = "ALIGNED"
        else:
            sequence_alignment = "OPPOSED"

        if "TD_ALIGNED_CONFIDENCE_UP" in codes:
            sequence_effect = "CONFIDENCE_UP"
        elif "TD_OPPOSED_CONFIDENCE_DOWN" in codes:
            sequence_effect = "CONFIDENCE_DOWN"
        elif sequence_active and action == "HOLD":
            sequence_effect = "NO_ACTION_TO_MODIFY"
        else:
            sequence_effect = "NONE"

        trend_event = (
            signal_type
            if signal_type.startswith(
                ("SHORT_", "LONG_", "DOUBLE_", "CONFLICTING_")
            )
            else "NO_NEW_TREND_EVENT"
        )
        return {
            "schema_version": "1.0",
            "detail_policy": "STATE_AND_EFFECTS_ONLY_NO_FORMULAS",
            "trend": {
                "market_state": trend.get("market", "SIDEWAYS"),
                "event": trend_event,
            },
            "structure": {
                "bottom": bottom,
                "top": top,
                "effect": structure_effect,
                "fraction_delta": round(
                    float(structure_fraction - primary_fraction), 4
                ),
                "confidence_delta": round(
                    float(structure_confidence - primary_confidence), 4
                ),
            },
            "sequence": {
                "count": count,
                "stage": sequence_stage,
                "direction": sequence_direction,
                "alignment": sequence_alignment,
                "effect": sequence_effect,
                "confidence_delta": round(
                    float(final_confidence - structure_confidence), 4
                ),
            },
            "decision": {
                "candidate_action": action,
                "signal_type": signal_type,
                "reason_codes": codes,
                "reason": reason,
                "primary_fraction": round(float(primary_fraction), 4),
                "after_structure_fraction": round(
                    float(structure_fraction), 4
                ),
                "final_fraction": round(float(structure_fraction), 4),
                "primary_confidence": round(float(primary_confidence), 4),
                "after_structure_confidence": round(
                    float(structure_confidence), 4
                ),
                "final_confidence": round(float(final_confidence), 4),
            },
        }

    @staticmethod
    def _decision(
        action: str,
        fraction: float,
        confidence: float,
        signal_type: str,
        reason_codes: Iterable[str],
        reason: str,
    ) -> Dict:
        return {
            "action": action,
            "signal_fraction": round(float(fraction), 4),
            "confidence": round(float(confidence), 4),
            "signal_type": signal_type,
            "reason_codes": list(reason_codes),
            "reason": reason,
        }

    def _validate_input(self, df: pd.DataFrame) -> None:
        if df is None:
            raise ValueError("daily bars are required")
        missing = self.REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(f"missing daily columns: {sorted(missing)}")
        if len(df) < self.config.minimum_bars:
            raise ValueError(
                f"insufficient daily bars: {len(df)} < {self.config.minimum_bars}"
            )
        if df[list(self.REQUIRED_COLUMNS)].tail(2).isna().any().any():
            raise ValueError("latest daily bars contain NaN")

    @staticmethod
    def _signal_asof(df: pd.DataFrame) -> str:
        if "date" in df.columns:
            return str(pd.to_datetime(df["date"].iloc[-1]).date())
        return str(pd.to_datetime(df.index[-1]).date())
