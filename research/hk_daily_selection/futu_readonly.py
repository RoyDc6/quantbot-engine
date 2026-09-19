"""Strictly read-only Futu data helpers.

Only ``OpenQuoteContext`` is used. This module has no account, position,
trading-context, or order capability.
"""

from __future__ import annotations

from datetime import datetime
import time
from typing import Dict, Iterable, List, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from .contracts import normalize_symbol, prepare_bars, prepare_membership


HONG_KONG_TZ = ZoneInfo("Asia/Hong_Kong")

# Earliest wall-clock time at which a same-day HK bar may be fetched.
# 16:15 HKT is the market close; 16:30 is the earliest a stable post-close
# bar can be retrieved. Any fetch whose wall time is before 16:15 is rejected
# unconditionally because intraday snapshots are not valid completed sessions.
HK_FETCH_EARLIEST_HKT = (16, 15)
HK_FETCH_SAME_DAY_HKT = (16, 30)


def _now_hkt() -> datetime:
    return datetime.now(HONG_KONG_TZ)


def _hkt_wall_clock(value: pd.Timestamp | datetime) -> tuple[int, int]:
    stamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value  # type: ignore[union-attr]
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=HONG_KONG_TZ)
    stamp = stamp.astimezone(HONG_KONG_TZ)
    return (stamp.hour, stamp.minute)


def _require_completed_session(session: pd.Timestamp) -> None:
    """Reject future sessions and same-day sessions before 16:15 HKT."""

    now = _now_hkt()
    requested = pd.Timestamp(session).date()
    if requested > now.date():
        raise ValueError("completed session cannot be in the future")
    if requested == now.date() and _hkt_wall_clock(now) < HK_FETCH_EARLIEST_HKT:
        raise ValueError(
            "today's HK daily bar is not treated as complete before 16:15 HKT"
        )


def _require_fetch_window(completed_session: pd.Timestamp) -> datetime:
    """Enforce the post-close fetch window for same-day bars.

    The rule (per WORKBUDDY_SYNC §四 / 2026-07-30 修正) is:

    * Sessions strictly before today have no wall-clock restriction — they
      are always historical bars and may be retrieved at any time, even
      outside market hours, e.g. when backfilling a frozen snapshot.
    * The same-day session (i.e. today) may only be fetched at or after
      ``HK_FETCH_SAME_DAY_HKT`` = 16:30 HKT.
    * Any fetch whose wall time is before 16:15 HKT is rejected outright
      when the requested session is today — no intraday snapshot is treated
      as a completed session.
    """

    now = _now_hkt()
    requested = pd.Timestamp(completed_session).date()
    if requested > now.date():
        raise ValueError("completed session cannot be in the future")
    if requested != now.date():
        # Historical session: no wall-clock restriction applies.
        return now
    wall = _hkt_wall_clock(now)
    if wall < HK_FETCH_EARLIEST_HKT:
        raise ValueError(
            "HK bar fetch is rejected before 16:15 HKT; intraday snapshots "
            "are not valid completed sessions"
        )
    if wall < HK_FETCH_SAME_DAY_HKT:
        raise ValueError(
            "same-day HK benchmark bar may only be fetched at or after "
            "16:30 HKT; current wall time is "
            f"{wall[0]:02d}:{wall[1]:02d} HKT"
        )
    return now


def _chunks(values: Sequence[str], size: int) -> Iterable[List[str]]:
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def _to_futu_code(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if not normalized.endswith(".HK"):
        raise ValueError(f"not an HK symbol: {symbol}")
    return f"HK.{normalized[:-3]}"


def fetch_current_hsci_membership(
    *,
    host: str = "127.0.0.1",
    port: int = 11111,
    index_code: str = "HK.800701",
    observed_asof: pd.Timestamp | None = None,
    screen_top_n: int = 50,
    recent_action_lookback_days: int = 90,
) -> pd.DataFrame:
    """Fetch the current HSCI constituent snapshot.

    The result is explicitly labeled as a current snapshot and must not be
    backfilled into historical tests.
    """

    observed_clock = _now_hkt()
    if observed_asof is not None:
        requested_asof = pd.Timestamp(observed_asof)
        if requested_asof.tzinfo is None:
            requested_asof = requested_asof.tz_localize(HONG_KONG_TZ)
        else:
            requested_asof = requested_asof.tz_convert(HONG_KONG_TZ)
        if requested_asof.date() != observed_clock.date():
            raise ValueError(
                "a current HSCI snapshot cannot be backdated; observed_asof "
                "must match the actual HKT observation date"
            )

    from futu import OpenQuoteContext, RET_OK

    asof = pd.Timestamp(observed_clock.date())

    if screen_top_n <= 0:
        raise ValueError("screen_top_n must be positive")
    if recent_action_lookback_days <= 0:
        raise ValueError("recent_action_lookback_days must be positive")

    context = OpenQuoteContext(host=host, port=port)
    try:
        ret, data = context.get_plate_stock(index_code)
        if ret != RET_OK:
            raise RuntimeError(f"Futu get_plate_stock failed: {data}")

        futu_codes = [str(value) for value in data["code"].tolist()]
        stock_futu_codes = [
            str(row.code)
            for row in data.itertuples(index=False)
            if str(row.stock_type).split(".")[-1].upper() == "STOCK"
        ]
        snapshot_frames: List[pd.DataFrame] = []
        for code_batch in _chunks(futu_codes, 200):
            ret, snapshot = context.get_market_snapshot(code_batch)
            if ret != RET_OK:
                raise RuntimeError(
                    f"Futu get_market_snapshot failed: {snapshot}"
                )
            snapshot_frames.append(snapshot)
        snapshots = pd.concat(snapshot_frames, ignore_index=True)

        owner_frames: List[pd.DataFrame] = []
        # get_owner_plate is limited to 10 calls per 30 seconds.  Six batches
        # keep a full HSCI snapshot inside that stable rate envelope.
        for code_batch in _chunks(stock_futu_codes, 100):
            ret, owners = context.get_owner_plate(code_batch)
            if ret != RET_OK:
                raise RuntimeError(f"Futu get_owner_plate failed: {owners}")
            if owners is not None and not owners.empty:
                owner_frames.append(owners)
        owners = (
            pd.concat(owner_frames, ignore_index=True)
            if owner_frames
            else pd.DataFrame(
                columns=["code", "plate_name", "plate_type"]
            )
        )

        snapshot_by_symbol: Dict[str, Dict[str, object]] = {}
        for row in snapshots.itertuples(index=False):
            normalized = normalize_symbol(row.code)
            snapshot_by_symbol[normalized] = {
                "total_market_val": getattr(row, "total_market_val", float("nan")),
                "snapshot_is_suspended": getattr(row, "suspension", pd.NA),
                "sec_status": str(getattr(row, "sec_status", "")),
            }

        industry_by_symbol: Dict[str, str] = {}
        if not owners.empty:
            plate_types = owners["plate_type"].map(
                lambda value: str(value).split(".")[-1].upper()
            )
            industries = owners[plate_types.eq("INDUSTRY")].copy()
            industries = industries.sort_values(["code", "plate_name"])
            for code, group in industries.groupby("code", sort=False):
                industry_by_symbol[normalize_symbol(code)] = str(
                    group.iloc[0]["plate_name"]
                )

        stock_symbols = []
        for row in data.itertuples(index=False):
            stock_type = str(row.stock_type).split(".")[-1].upper()
            if stock_type == "STOCK":
                stock_symbols.append(normalize_symbol(row.code))
        ranked_symbols = sorted(
            stock_symbols,
            key=lambda symbol: (
                -float(
                    snapshot_by_symbol.get(symbol, {}).get(
                        "total_market_val", float("nan")
                    )
                )
                if pd.notna(
                    snapshot_by_symbol.get(symbol, {}).get(
                        "total_market_val", float("nan")
                    )
                )
                else float("inf"),
                symbol,
            ),
        )
        rank_by_symbol = {
            symbol: rank
            for rank, symbol in enumerate(ranked_symbols, start=1)
        }

        action_by_symbol: Dict[str, Dict[str, object]] = {}
        lookback_start = asof - pd.Timedelta(
            days=recent_action_lookback_days
        )
        for action_index, symbol in enumerate(
            ranked_symbols[:screen_top_n]
        ):
            if action_index:
                # Futu get_rehab accepts one symbol per call and is subject to
                # the same conservative 10-calls/30-second quote envelope.
                time.sleep(3.05)
            futu_code = _to_futu_code(symbol)
            ret, rehab = context.get_rehab(futu_code)
            if ret != RET_OK:
                action_by_symbol[symbol] = {
                    "corporate_action_status": "UNKNOWN",
                    "has_recent_corporate_action": pd.NA,
                    "last_corporate_action_date": pd.NaT,
                }
                continue
            action_dates = (
                pd.to_datetime(rehab["ex_div_date"], errors="coerce")
                .dropna()
                .dt.normalize()
                if rehab is not None and not rehab.empty
                else pd.Series(dtype="datetime64[ns]")
            )
            action_dates = action_dates[action_dates <= asof]
            recent = action_dates[action_dates >= lookback_start]
            action_by_symbol[symbol] = {
                "corporate_action_status": (
                    "RECENT_ACTION" if not recent.empty else "CLEAR"
                ),
                "has_recent_corporate_action": not recent.empty,
                "last_corporate_action_date": (
                    action_dates.max() if not action_dates.empty else pd.NaT
                ),
            }
    finally:
        context.close()

    evidence_clock = _now_hkt().isoformat(timespec="seconds")
    rows = []
    for row in data.itertuples(index=False):
        stock_type = str(row.stock_type).split(".")[-1].upper()
        normalized = normalize_symbol(row.code)
        snapshot = snapshot_by_symbol.get(normalized, {})
        rank = rank_by_symbol.get(normalized, float("nan"))
        action = action_by_symbol.get(
            normalized,
            {
                "corporate_action_status": "NOT_IN_SCREEN_UNIVERSE",
                "has_recent_corporate_action": pd.NA,
                "last_corporate_action_date": pd.NaT,
            },
        )
        rows.append(
            {
                "symbol": normalized,
                "effective_from": asof,
                "effective_to": "",
                "name": str(row.stock_name),
                "sector": industry_by_symbol.get(normalized, ""),
                "sector_source": (
                    "FUTU_OWNER_PLATE_INDUSTRY"
                    if normalized in industry_by_symbol
                    else ""
                ),
                "security_type": stock_type,
                "issuer_id": normalized,
                "lot_size": int(row.lot_size),
                "is_current_snapshot": True,
                "source_asof": asof,
                "observed_at_hkt": observed_clock.isoformat(
                    timespec="seconds"
                ),
                "membership_source": f"FUTU_CURRENT_INDEX:{index_code}",
                "total_market_val": snapshot.get(
                    "total_market_val", float("nan")
                ),
                "screen_universe_rank": rank,
                "screen_universe_source": (
                    f"HSCI_TOP_{screen_top_n}_BY_TOTAL_MARKET_VAL"
                    if pd.notna(rank) and int(rank) <= screen_top_n
                    else ""
                ),
                "snapshot_is_suspended": snapshot.get(
                    "snapshot_is_suspended", pd.NA
                ),
                "suspension_source": (
                    "FUTU_MARKET_SNAPSHOT" if snapshot else ""
                ),
                "market_snapshot_observed_at_hkt": (
                    evidence_clock if snapshot else ""
                ),
                "sec_status": snapshot.get("sec_status", ""),
                "corporate_action_status": action[
                    "corporate_action_status"
                ],
                "has_recent_corporate_action": action[
                    "has_recent_corporate_action"
                ],
                "last_corporate_action_date": action[
                    "last_corporate_action_date"
                ],
                "corporate_action_observed_at_hkt": (
                    evidence_clock
                    if normalized in action_by_symbol
                    else ""
                ),
            }
        )
    return prepare_membership(pd.DataFrame(rows))


def fetch_history_bars(
    symbols: Iterable[str],
    *,
    start: pd.Timestamp,
    completed_session: pd.Timestamp,
    host: str = "127.0.0.1",
    port: int = 11111,
    adjustment: str = "NONE",
) -> pd.DataFrame:
    """Fetch historical completed daily bars for an explicit symbol list.

    Futu history quota is consumed per distinct security. Callers should cache
    results and inspect their quota before broad-universe requests.
    """

    from futu import (
        AuType,
        KLType,
        KL_FIELD,
        OpenQuoteContext,
        RET_OK,
    )

    end = pd.Timestamp(completed_session).normalize()
    fetch_clock = _require_fetch_window(end)
    start_date = pd.Timestamp(start).normalize()
    if start_date > end:
        raise ValueError("start must be on or before completed_session")
    adjustment_map = {
        "NONE": AuType.NONE,
        "QFQ": AuType.QFQ,
        "HFQ": AuType.HFQ,
    }
    adjustment_key = adjustment.upper()
    if adjustment_key not in adjustment_map:
        raise ValueError("adjustment must be NONE, QFQ, or HFQ")

    context = OpenQuoteContext(host=host, port=port)
    frames: List[pd.DataFrame] = []
    try:
        for symbol_index, symbol in enumerate(symbols):
            if symbol_index:
                # Pace history calls so a 51-symbol screen cache remains
                # comfortably below OpenD request-frequency limits.
                time.sleep(1.05)
            normalized = normalize_symbol(symbol)
            if not normalized.endswith(".HK"):
                raise ValueError(f"not an HK symbol: {symbol}")
            futu_code = _to_futu_code(normalized)
            page_key = None
            pages = []
            while True:
                ret, data, page_key = context.request_history_kline(
                    futu_code,
                    start=start_date.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                    ktype=KLType.K_DAY,
                    autype=adjustment_map[adjustment_key],
                    fields=[KL_FIELD.ALL],
                    max_count=1000,
                    page_req_key=page_key,
                )
                if ret != RET_OK:
                    raise RuntimeError(
                        f"Futu history failed for {futu_code}: {data}"
                    )
                if data is not None and not data.empty:
                    pages.append(data)
                if not page_key:
                    break
            if not pages:
                continue
            raw = pd.concat(pages, ignore_index=True)
            frame = pd.DataFrame(
                {
                    "date": pd.to_datetime(raw["time_key"]).dt.normalize(),
                    "symbol": normalized,
                    "open": raw["open"],
                    "high": raw["high"],
                    "low": raw["low"],
                    "close": raw["close"],
                    "volume": raw["volume"],
                    "turnover": raw["turnover"],
                }
            )
            frame = frame[frame["date"] <= end]
            # Note: ``is_suspended`` is intentionally NOT inferred from
            # volume=0 / turnover=0. Per WORKBUDDY_SYNC §四 (2026-07-30 修正),
            # zero activity is a quality warning, not a suspension signal.
            # Suspension must come from an explicit suspended-flag field or an
            # externally maintained suspension ledger.
            if "is_suspended" in raw.columns:
                frame["is_suspended"] = raw["is_suspended"]
                frame["suspension_source"] = "FUTU_HISTORY_EXPLICIT"
            else:
                frame["is_suspended"] = pd.NA
                frame["suspension_source"] = ""
            frame["adjustment"] = adjustment_key
            frame["source"] = "FUTU_HISTORY"
            frame["fetched_at_hkt"] = fetch_clock.isoformat(timespec="seconds")
            frames.append(frame)
    finally:
        context.close()

    if not frames:
        raise ValueError("Futu returned no historical bars")
    return prepare_bars(
        pd.concat(frames, ignore_index=True),
        repair_ohlc_envelope=True,
        max_relative_envelope_gap=0.005,
    )
