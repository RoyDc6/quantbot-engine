"""Data contracts and validation for point-in-time Hong Kong research."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


HONG_KONG_TZ = ZoneInfo("Asia/Hong_Kong")
SCREEN_FETCH_SAME_DAY_HKT = (16, 30)

BAR_REQUIRED = {
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
}

MEMBERSHIP_REQUIRED = {
    "symbol",
    "effective_from",
    "effective_to",
    "name",
    "sector",
}


@dataclass(frozen=True)
class DataQualityResult:
    ok: bool
    errors: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()

    def require(self) -> None:
        if not self.ok:
            raise ValueError("; ".join(self.errors))


def normalize_symbol(value: object) -> str:
    """Normalize HK symbols to ``00700.HK``.

    Benchmark identifiers that do not look like numeric HK stock codes are
    preserved after trimming and upper-casing.
    """

    raw = str(value).strip().upper()
    if raw.startswith("HK.") and raw[3:].isdigit():
        return f"{int(raw[3:]):05d}.HK"
    if raw.endswith(".HK") and raw[:-3].isdigit():
        return f"{int(raw[:-3]):05d}.HK"
    if raw.isdigit():
        return f"{int(raw):05d}.HK"
    return raw


def _normalize_bool(series: pd.Series, default: bool = False) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(default)
    truthy = {"1", "TRUE", "T", "YES", "Y"}
    falsy = {"0", "FALSE", "F", "NO", "N", ""}

    def parse(value: object) -> bool:
        if pd.isna(value):
            return default
        text = str(value).strip().upper()
        if text in truthy:
            return True
        if text in falsy:
            return False
        raise ValueError(f"invalid boolean value: {value!r}")

    return series.map(parse)


def _normalize_nullable_bool(series: pd.Series) -> pd.Series:
    """Normalize boolean evidence while preserving unknown values."""

    truthy = {"1", "TRUE", "T", "YES", "Y"}
    falsy = {"0", "FALSE", "F", "NO", "N"}

    def parse(value: object) -> object:
        if pd.isna(value) or str(value).strip() == "":
            return pd.NA
        if isinstance(value, (bool, np.bool_)):
            return bool(value)
        text = str(value).strip().upper()
        if text in truthy:
            return True
        if text in falsy:
            return False
        raise ValueError(f"invalid nullable boolean value: {value!r}")

    return series.map(parse).astype("boolean")


def _parse_hkt_timestamp(value: object) -> pd.Timestamp:
    """Normalize a timestamp to an aware Asia/Hong_Kong timestamp."""

    if pd.isna(value) or str(value).strip() == "":
        return pd.NaT
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize(HONG_KONG_TZ)
    return stamp.tz_convert(HONG_KONG_TZ)


def prepare_bars(
    frame: pd.DataFrame,
    *,
    repair_ohlc_envelope: bool = False,
    max_relative_envelope_gap: float = 0.005,
) -> pd.DataFrame:
    missing = BAR_REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(f"bars missing required columns: {sorted(missing)}")

    bars = frame.copy()
    bars["date"] = pd.to_datetime(bars["date"], errors="raise").dt.normalize()
    bars["symbol"] = bars["symbol"].map(normalize_symbol)
    numeric = ["open", "high", "low", "close", "volume", "turnover"]
    for column in numeric:
        bars[column] = pd.to_numeric(bars[column], errors="raise")
    if max_relative_envelope_gap < 0.0:
        raise ValueError("max_relative_envelope_gap cannot be negative")
    if "source_high" not in bars:
        bars["source_high"] = bars["high"]
    else:
        bars["source_high"] = pd.to_numeric(
            bars["source_high"], errors="coerce"
        ).fillna(bars["high"])
    if "source_low" not in bars:
        bars["source_low"] = bars["low"]
    else:
        bars["source_low"] = pd.to_numeric(
            bars["source_low"], errors="coerce"
        ).fillna(bars["low"])
    if "ohlc_envelope_repaired" in bars:
        repaired = _normalize_bool(
            bars["ohlc_envelope_repaired"], default=False
        )
    else:
        repaired = pd.Series(False, index=bars.index, dtype=bool)
    expected_high = bars[["open", "close", "low"]].max(axis=1)
    expected_low = bars[["open", "close", "high"]].min(axis=1)
    invalid_high = bars["high"] < expected_high
    invalid_low = bars["low"] > expected_low
    high_gap = (
        (expected_high - bars["high"]) / expected_high.clip(lower=1e-12)
    )
    low_gap = (
        (bars["low"] - expected_low) / expected_low.clip(lower=1e-12)
    )
    repairable = (
        (~invalid_high | (high_gap <= max_relative_envelope_gap))
        & (~invalid_low | (low_gap <= max_relative_envelope_gap))
    )
    invalid = invalid_high | invalid_low
    if repair_ohlc_envelope and (invalid & ~repairable).any():
        sample = bars.loc[
            invalid & ~repairable,
            ["date", "symbol", "open", "high", "low", "close"],
        ].head(3)
        raise ValueError(
            "OHLC envelope gap exceeds repair tolerance: "
            f"{sample.to_dict('records')}"
        )
    if repair_ohlc_envelope and invalid.any():
        bars.loc[invalid_high, "high"] = expected_high[invalid_high]
        bars.loc[invalid_low, "low"] = expected_low[invalid_low]
        repaired = repaired | invalid
    bars["ohlc_envelope_repaired"] = repaired.astype(bool)
    if "is_suspended" not in bars:
        bars["is_suspended"] = pd.Series(
            pd.NA, index=bars.index, dtype="boolean"
        )
    else:
        bars["is_suspended"] = _normalize_nullable_bool(
            bars["is_suspended"]
        )
    if "suspension_source" not in bars:
        bars["suspension_source"] = ""
    else:
        bars["suspension_source"] = (
            bars["suspension_source"].fillna("").astype(str)
        )
    if "fetched_at_hkt" in bars:
        bars["fetched_at_hkt"] = bars["fetched_at_hkt"].map(
            _parse_hkt_timestamp
        )
    bars = bars.sort_values(["symbol", "date"]).reset_index(drop=True)

    quality = validate_bars(bars)
    quality.require()
    return bars


def validate_bars(bars: pd.DataFrame) -> DataQualityResult:
    errors: List[str] = []
    warnings: List[str] = []

    duplicates = bars.duplicated(["date", "symbol"])
    if duplicates.any():
        sample = bars.loc[duplicates, ["date", "symbol"]].head(3)
        errors.append(f"duplicate date-symbol rows: {sample.to_dict('records')}")

    price_columns = ["open", "high", "low", "close"]
    if (~np.isfinite(bars[price_columns].to_numpy(dtype=float))).any():
        errors.append("OHLC contains non-finite values")
    if (bars[price_columns] <= 0).any().any():
        errors.append("OHLC must be positive")
    invalid_high = bars["high"] < bars[["open", "close", "low"]].max(
        axis=1
    )
    if invalid_high.any():
        sample = bars.loc[
            invalid_high,
            ["date", "symbol", "open", "high", "low", "close"],
        ].head(3)
        errors.append(
            "high is below open/close/low: "
            f"{sample.to_dict('records')}"
        )
    invalid_low = bars["low"] > bars[["open", "close", "high"]].min(
        axis=1
    )
    if invalid_low.any():
        sample = bars.loc[
            invalid_low,
            ["date", "symbol", "open", "high", "low", "close"],
        ].head(3)
        errors.append(
            "low is above open/close/high: "
            f"{sample.to_dict('records')}"
        )
    if (bars[["volume", "turnover"]] < 0).any().any():
        errors.append("volume and turnover must be non-negative")

    zero_activity = (bars["volume"] == 0) | (bars["turnover"] == 0)
    if zero_activity.any():
        warnings.append(
            f"{int(zero_activity.sum())} rows have zero volume or turnover"
        )
    unknown_suspension = bars["is_suspended"].isna()
    if unknown_suspension.any():
        warnings.append(
            f"{int(unknown_suspension.sum())} rows have unknown suspension "
            "status; a daily screen needs snapshot evidence"
        )
    repaired_envelopes = bars.get(
        "ohlc_envelope_repaired", pd.Series(False, index=bars.index)
    ).fillna(False).astype(bool)
    if repaired_envelopes.any():
        warnings.append(
            f"{int(repaired_envelopes.sum())} rows had bounded OHLC envelope "
            "repairs with original high/low preserved"
        )

    return DataQualityResult(not errors, tuple(errors), tuple(warnings))


def validate_screen_bar_freshness(
    bars: pd.DataFrame,
    session_date: pd.Timestamp,
) -> DataQualityResult:
    """Validate fetch provenance for every bar used on a screen date.

    The daily screen must never promote an intraday cache merely because the
    wall clock is now past the close. Every row for ``SESSION_DATE`` therefore
    needs a fetch timestamp. A same-day fetch must be at or after 16:30 HKT;
    a later historical re-fetch is allowed.
    """

    session = pd.Timestamp(session_date).normalize()
    dated = bars[bars["date"] == session]
    errors: List[str] = []
    warnings: List[str] = []

    if dated.empty:
        errors.append(f"bars contain no rows for screen date {session.date()}")
        return DataQualityResult(False, tuple(errors), tuple(warnings))

    if "fetched_at_hkt" not in dated.columns:
        errors.append(
            "screen bars missing fetched_at_hkt provenance for "
            f"{session.date()}"
        )
        return DataQualityResult(False, tuple(errors), tuple(warnings))

    fetched = dated["fetched_at_hkt"].map(_parse_hkt_timestamp)
    missing = fetched.isna()
    if missing.any():
        errors.append(
            f"{int(missing.sum())} screen bar row(s) have missing "
            f"fetched_at_hkt on {session.date()}"
        )

    before_session = 0
    early_same_day = 0
    adjusted_future_fetch = 0
    adjustments = dated.get(
        "adjustment", pd.Series("NONE", index=dated.index)
    ).fillna("NONE").astype(str).str.upper()
    for index, stamp in fetched.dropna().items():
        fetch_date = stamp.date()
        if fetch_date < session.date():
            before_session += 1
        elif (
            fetch_date == session.date()
            and (stamp.hour, stamp.minute) < SCREEN_FETCH_SAME_DAY_HKT
        ):
            early_same_day += 1
        if adjustments.loc[index] in {"QFQ", "HFQ"} and (
            fetch_date > session.date()
        ):
            adjusted_future_fetch += 1

    if before_session:
        errors.append(
            f"{before_session} screen bar row(s) were fetched before "
            f"SESSION_DATE {session.date()}"
        )
    if early_same_day:
        errors.append(
            f"{early_same_day} same-day screen bar row(s) were fetched "
            "before 16:30 HKT"
        )
    if adjusted_future_fetch:
        errors.append(
            f"{adjusted_future_fetch} adjusted screen bar row(s) were "
            "fetched after SESSION_DATE and may encode future corporate "
            "actions"
        )

    return DataQualityResult(not errors, tuple(errors), tuple(warnings))


def prepare_membership(frame: pd.DataFrame) -> pd.DataFrame:
    missing = MEMBERSHIP_REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(
            f"membership missing required columns: {sorted(missing)}"
        )

    membership = frame.copy()
    membership["symbol"] = membership["symbol"].map(normalize_symbol)
    membership["effective_from"] = pd.to_datetime(
        membership["effective_from"], errors="raise"
    ).dt.normalize()
    membership["effective_to"] = pd.to_datetime(
        membership["effective_to"], errors="coerce"
    ).dt.normalize()
    membership["effective_to"] = membership["effective_to"].fillna(
        pd.Timestamp.max.normalize()
    )
    membership["name"] = membership["name"].fillna("").astype(str)
    membership["sector"] = membership["sector"].fillna("").astype(str)
    if "security_type" not in membership:
        membership["security_type"] = "STOCK"
    membership["security_type"] = (
        membership["security_type"].fillna("UNKNOWN").astype(str).str.upper()
    )
    if "issuer_id" not in membership:
        membership["issuer_id"] = membership["symbol"]
    membership["issuer_id"] = membership["issuer_id"].fillna(
        membership["symbol"]
    ).astype(str)
    if "lot_size" in membership:
        membership["lot_size"] = pd.to_numeric(
            membership["lot_size"], errors="coerce"
        )
    else:
        membership["lot_size"] = np.nan
    if "is_current_snapshot" in membership:
        membership["is_current_snapshot"] = _normalize_bool(
            membership["is_current_snapshot"]
        )
    else:
        membership["is_current_snapshot"] = False
    if "source_asof" in membership:
        membership["source_asof"] = pd.to_datetime(
            membership["source_asof"], errors="coerce"
        ).dt.normalize()
    else:
        membership["source_asof"] = pd.NaT
    if "observed_at_hkt" in membership:
        membership["observed_at_hkt"] = membership["observed_at_hkt"].map(
            _parse_hkt_timestamp
        )
    else:
        membership["observed_at_hkt"] = pd.NaT
    if "membership_source" not in membership:
        membership["membership_source"] = ""
    optional_numeric = ["total_market_val", "screen_universe_rank"]
    for column in optional_numeric:
        if column in membership:
            membership[column] = pd.to_numeric(
                membership[column], errors="coerce"
            )
        else:
            membership[column] = np.nan
    optional_nullable_bool = [
        "snapshot_is_suspended",
        "has_recent_corporate_action",
    ]
    for column in optional_nullable_bool:
        if column in membership:
            membership[column] = _normalize_nullable_bool(membership[column])
        else:
            membership[column] = pd.Series(
                pd.NA, index=membership.index, dtype="boolean"
            )
    optional_timestamps = [
        "market_snapshot_observed_at_hkt",
        "corporate_action_observed_at_hkt",
    ]
    for column in optional_timestamps:
        if column in membership:
            membership[column] = membership[column].map(
                _parse_hkt_timestamp
            )
        else:
            membership[column] = pd.NaT
    if "last_corporate_action_date" in membership:
        membership["last_corporate_action_date"] = pd.to_datetime(
            membership["last_corporate_action_date"], errors="coerce"
        ).dt.normalize()
    else:
        membership["last_corporate_action_date"] = pd.NaT
    optional_text = [
        "sector_source",
        "suspension_source",
        "sec_status",
        "corporate_action_status",
        "screen_universe_source",
    ]
    for column in optional_text:
        if column not in membership:
            membership[column] = ""
        else:
            membership[column] = (
                membership[column].fillna("").astype(str)
            )
    membership["corporate_action_status"] = (
        membership["corporate_action_status"].str.strip().str.upper()
    )

    membership = membership.sort_values(
        ["symbol", "effective_from", "effective_to"]
    ).reset_index(drop=True)
    quality = validate_membership(membership)
    quality.require()
    return membership


def validate_membership(membership: pd.DataFrame) -> DataQualityResult:
    errors: List[str] = []
    warnings: List[str] = []

    if (membership["effective_from"] > membership["effective_to"]).any():
        errors.append("membership effective_from is after effective_to")
    duplicates = membership.duplicated(
        ["symbol", "effective_from", "effective_to"]
    )
    if duplicates.any():
        errors.append("duplicate membership intervals")

    for symbol, group in membership.groupby("symbol", sort=False):
        ordered = group.sort_values("effective_from")
        prior_end: Optional[pd.Timestamp] = None
        for row in ordered.itertuples():
            if prior_end is not None and row.effective_from <= prior_end:
                errors.append(f"overlapping membership intervals for {symbol}")
                break
            prior_end = row.effective_to

    if membership["sector"].eq("").any():
        warnings.append("membership contains blank sectors; sector cap may be partial")
    if membership["membership_source"].eq("").all():
        warnings.append("membership_source is blank")
    if membership["total_market_val"].isna().any():
        warnings.append(
            "membership contains missing total_market_val; Top-N universe "
            "ranking may be partial"
        )
    if membership["snapshot_is_suspended"].isna().any():
        warnings.append(
            "membership contains unknown snapshot suspension status"
        )
    known_actions = membership["corporate_action_status"].isin(
        ["CLEAR", "RECENT_ACTION"]
    )
    if (~known_actions).any():
        warnings.append(
            "membership contains unknown corporate-action evidence"
        )

    return DataQualityResult(not errors, tuple(errors), tuple(warnings))


def membership_at(membership: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
    asof = pd.Timestamp(date).normalize()
    active = membership[
        (membership["effective_from"] <= asof)
        & (membership["effective_to"] >= asof)
    ]
    return active.copy()


def validate_point_in_time_coverage(
    membership: pd.DataFrame,
    trading_dates: Iterable[pd.Timestamp],
    *,
    allow_survivorship_bias: bool = False,
    mode: str = "backtest",
) -> DataQualityResult:
    """Validate that membership covers the requested trading dates.

    Parameters
    ----------
    mode:
        ``"screen"`` validates only the single latest trading date (used for
        daily research output). ``"backtest"`` validates every supplied date
        (used for historical research). Default is ``"backtest"`` to keep the
        historical contract strict.
    """

    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"screen", "backtest"}:
        raise ValueError("mode must be 'screen' or 'backtest'")

    dates = pd.DatetimeIndex(pd.to_datetime(list(trading_dates))).normalize()
    if len(dates) == 0:
        return DataQualityResult(False, ("no trading dates supplied",), ())

    errors: List[str] = []
    warnings: List[str] = []
    if normalized_mode == "screen":
        # Daily screen: only the last completed trading date must be covered.
        session_date = dates.max()
        check_dates = pd.DatetimeIndex([session_date])
    else:
        check_dates = dates
    start = check_dates.min()

    if normalized_mode == "screen":
        session_date = check_dates.max()
        active = membership_at(membership, session_date)
        current_active = active[active["is_current_snapshot"]]
        if not current_active.empty:
            missing_source_asof = current_active["source_asof"].isna()
            if missing_source_asof.any():
                errors.append(
                    f"{int(missing_source_asof.sum())} active current "
                    "snapshot row(s) have missing source_asof"
                )
            future_source_asof = (
                current_active["source_asof"].notna()
                & (current_active["source_asof"] > session_date)
            )
            if future_source_asof.any():
                errors.append(
                    f"{int(future_source_asof.sum())} active current "
                    "snapshot row(s) have source_asof later than "
                    f"SESSION_DATE {session_date.date()}"
                )
            missing_observed_at = current_active["observed_at_hkt"].isna()
            if missing_observed_at.any():
                errors.append(
                    f"{int(missing_observed_at.sum())} active current "
                    "snapshot row(s) have missing observed_at_hkt"
                )

    snapshots = membership[membership["is_current_snapshot"]]
    if not snapshots.empty:
        raw_source_dates = snapshots["source_asof"]
        if not pd.api.types.is_datetime64_any_dtype(raw_source_dates):
            raw_source_dates = pd.to_datetime(
                raw_source_dates, errors="coerce"
            )
        source_dates = raw_source_dates.dropna()
        earliest_snapshot = (
            source_dates.min()
            if not source_dates.empty
            else snapshots["effective_from"].min()
        )
        if pd.notna(earliest_snapshot) and start < earliest_snapshot:
            message = (
                "current constituent snapshot does not provide point-in-time "
                f"coverage before {earliest_snapshot.date()}"
            )
            if allow_survivorship_bias:
                warnings.append(f"SURVIVORSHIP_BIAS: {message}")
            else:
                errors.append(message)

    uncovered = [
        date for date in check_dates if membership_at(membership, date).empty
    ]
    if uncovered:
        message = (
            f"membership has no active constituents on {len(uncovered)} trading "
            f"date(s); first={uncovered[0].date()}"
        )
        if allow_survivorship_bias:
            warnings.append(f"PARTIAL: {message}")
        else:
            errors.append(message)

    return DataQualityResult(not errors, tuple(errors), tuple(warnings))


def validate_pit_for_screen(
    membership: pd.DataFrame,
    session_date: pd.Timestamp,
    *,
    allow_survivorship_bias: bool = False,
) -> DataQualityResult:
    """PIT check for the daily screen: a single SESSION_DATE."""

    return validate_point_in_time_coverage(
        membership,
        [pd.Timestamp(session_date)],
        allow_survivorship_bias=allow_survivorship_bias,
        mode="screen",
    )


def validate_pit_for_backtest(
    membership: pd.DataFrame,
    trading_dates: Iterable[pd.Timestamp],
    *,
    allow_survivorship_bias: bool = False,
) -> DataQualityResult:
    """PIT check for the full backtest: every supplied trading date."""

    return validate_point_in_time_coverage(
        membership,
        trading_dates,
        allow_survivorship_bias=allow_survivorship_bias,
        mode="backtest",
    )


def load_bars_csv(path: Path | str) -> pd.DataFrame:
    return prepare_bars(pd.read_csv(Path(path)))


def load_membership_csv(path: Path | str) -> pd.DataFrame:
    return prepare_membership(pd.read_csv(Path(path)))
