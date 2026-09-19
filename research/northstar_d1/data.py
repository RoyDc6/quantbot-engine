"""Fail-closed Futu live data access for Northstar-D1."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import math
import socket
from typing import Any, Dict, Iterable, List, Sequence
import uuid
from zoneinfo import ZoneInfo

import pandas as pd

from core.futu_adapter import FutuAdapter
from core.universe_manager import UniverseManager

from .config import NorthstarD1Config


MARKET_TIMEZONES = {
    "HK": ZoneInfo("Asia/Hong_Kong"),
    "US": ZoneInfo("America/New_York"),
}

LIVE_MARKET_STATES = {
    "AUCTION",
    "MORNING",
    "AFTERNOON",
    "HK_CAS",
    "PRE_MARKET_BEGIN",
    "AFTER_HOURS_BEGIN",
    "NIGHT",
    "OVERNIGHT",
    "TRADE_AT_LAST",
}


class MarketDataFreshnessError(RuntimeError):
    """Raised when the mandatory live-Futu contract cannot be proven."""


def _market_now(market: str, value: datetime | None = None) -> datetime:
    timezone_info = MARKET_TIMEZONES[market.upper()]
    if value is None:
        return datetime.now(timezone_info)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone_info)
    return value.astimezone(timezone_info)


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text if text and text.upper() not in {"N/A", "NAN", "NONE"} else None


def _finite_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketDataFreshnessError(f"Futu {label} is not numeric: {value}") from exc
    if not math.isfinite(number):
        raise MarketDataFreshnessError(f"Futu {label} is not finite: {value}")
    return number


def expected_completed_session(
    trading_days: Sequence[Dict[str, Any]],
    market: str,
    *,
    queried_at: datetime,
    config: NorthstarD1Config | None = None,
) -> str:
    """Resolve the latest completed exchange session from Futu's calendar."""

    cfg = config or NorthstarD1Config()
    market = market.upper()
    local_now = _market_now(market, queried_at)
    sessions: Dict[Any, str] = {}
    for item in trading_days:
        raw_date = item.get("time")
        parsed = pd.to_datetime(raw_date, errors="coerce")
        if pd.isna(parsed):
            continue
        sessions[parsed.date()] = str(item.get("trade_date_type") or "WHOLE").upper()
    if not sessions:
        raise MarketDataFreshnessError("Futu trading calendar returned no valid sessions")

    today = local_now.date()
    eligible = [session for session in sessions if session < today]
    if today in sessions:
        session_type = sessions[today]
        if session_type == "MORNING":
            if market == "HK":
                cutoff = time(
                    cfg.hk_morning_session_cutoff_hour,
                    cfg.hk_morning_session_cutoff_minute,
                )
            else:
                cutoff = time(
                    cfg.us_morning_session_cutoff_hour,
                    cfg.us_morning_session_cutoff_minute,
                )
        else:
            cutoff = time(cfg.session_close_hour, cfg.session_close_minute)
        if local_now.time() >= cutoff:
            eligible.append(today)
    if not eligible:
        raise MarketDataFreshnessError(
            f"Futu calendar has no completed {market} session before {local_now.isoformat()}"
        )
    return max(eligible).isoformat()


def evaluate_snapshot_freshness(
    *,
    update_time: Any,
    market: str,
    market_state: str,
    queried_at: datetime,
    config: NorthstarD1Config | None = None,
) -> Dict[str, Any]:
    """Validate last-update age during live sessions and explain closed sessions."""

    cfg = config or NorthstarD1Config()
    market = market.upper()
    timezone_info = MARKET_TIMEZONES[market]
    update_text = _clean_text(update_time)
    if update_text is None:
        raise MarketDataFreshnessError("Futu snapshot has no update_time")
    parsed = pd.to_datetime(update_text, errors="coerce")
    if pd.isna(parsed):
        raise MarketDataFreshnessError(
            f"Futu snapshot update_time is invalid: {update_text}"
        )
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize(timezone_info)
    else:
        parsed = parsed.tz_convert(timezone_info)
    local_query = _market_now(market, queried_at)
    age_seconds = (local_query - parsed.to_pydatetime()).total_seconds()
    if age_seconds < -cfg.snapshot_future_tolerance_seconds:
        raise MarketDataFreshnessError(
            f"Futu snapshot timestamp is {abs(age_seconds):.1f}s in the future"
        )

    state = str(market_state or "UNKNOWN").upper()
    market_live = state in LIVE_MARKET_STATES
    if market_live and age_seconds > cfg.snapshot_max_age_seconds:
        raise MarketDataFreshnessError(
            f"Futu snapshot is stale during {state}: age={age_seconds:.1f}s "
            f"> {cfg.snapshot_max_age_seconds}s"
        )
    return {
        "update_time": parsed.isoformat(),
        "age_seconds": round(age_seconds, 3),
        "market_state": state,
        "market_live": market_live,
        "age_limit_seconds": (
            cfg.snapshot_max_age_seconds if market_live else None
        ),
        "freshness_basis": (
            "SNAPSHOT_AGE_WITHIN_LIVE_SESSION_LIMIT"
            if market_live
            else "LIVE_OPEND_RESPONSE_MARKET_NOT_TRADING"
        ),
    }


class FutuLiveFreshnessProbe:
    """Make a new, uncached OpenD quote/calendar request for every symbol fetch."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        config: NorthstarD1Config | None = None,
    ):
        self.host = host
        self.port = int(port)
        self.config = config or NorthstarD1Config()

    def query(self, symbol: str, market: str) -> Dict[str, Any]:
        try:
            import futu as ft
        except ImportError as exc:
            raise MarketDataFreshnessError("futu-api is not installed") from exc

        market = market.upper()
        if market not in MARKET_TIMEZONES:
            raise MarketDataFreshnessError(f"unsupported Futu market: {market}")
        request_id = uuid.uuid4().hex
        started_at = datetime.now(timezone.utc)
        local_started = _market_now(market, started_at)
        calendar_start = (
            local_started.date()
            - timedelta(days=self.config.trading_calendar_lookback_days)
        ).isoformat()
        calendar_end = local_started.date().isoformat()

        try:
            connection = socket.create_connection(
                (self.host, self.port),
                timeout=max(float(self.config.futu_tcp_timeout_seconds), 0.1),
            )
            connection.close()
        except OSError as exc:
            raise MarketDataFreshnessError(
                f"Futu OpenD TCP unavailable at {self.host}:{self.port}: {exc}"
            ) from exc

        context = None
        try:
            context = ft.OpenQuoteContext(host=self.host, port=self.port)
            state_ret, global_state = context.get_global_state()
            if state_ret != ft.RET_OK or not isinstance(global_state, dict):
                raise MarketDataFreshnessError(
                    f"Futu global-state query failed: {global_state}"
                )

            futu_code = FutuAdapter._to_futu(symbol)
            snapshot_ret, snapshot = context.get_market_snapshot([futu_code])
            if snapshot_ret != ft.RET_OK or snapshot is None or len(snapshot) != 1:
                raise MarketDataFreshnessError(
                    f"Futu snapshot query failed for {futu_code}: {snapshot}"
                )

            futu_market = ft.Market.HK if market == "HK" else ft.Market.US
            calendar_ret, trading_days = context.request_trading_days(
                market=futu_market,
                start=calendar_start,
                end=calendar_end,
            )
            if calendar_ret != ft.RET_OK or not isinstance(trading_days, list):
                raise MarketDataFreshnessError(
                    f"Futu trading-calendar query failed for {market}: {trading_days}"
                )
        except MarketDataFreshnessError:
            raise
        except Exception as exc:
            raise MarketDataFreshnessError(f"Futu live query failed: {exc}") from exc
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass

        queried_at = datetime.now(timezone.utc)
        if not bool(global_state.get("qot_logined")):
            raise MarketDataFreshnessError("Futu quote service is not logged in")
        program_status = str(global_state.get("program_status_type") or "").upper()
        if program_status != "READY":
            raise MarketDataFreshnessError(
                f"Futu OpenD program status is not READY: {program_status or 'UNKNOWN'}"
            )

        server_timestamp = _finite_float(
            global_state.get("timestamp"), "OpenD server timestamp"
        )
        server_time = datetime.fromtimestamp(server_timestamp, timezone.utc)
        clock_skew = abs((queried_at - server_time).total_seconds())
        if clock_skew > self.config.opend_max_clock_skew_seconds:
            raise MarketDataFreshnessError(
                f"Futu OpenD server clock is stale: skew={clock_skew:.1f}s "
                f"> {self.config.opend_max_clock_skew_seconds}s"
            )

        row = snapshot.iloc[0]
        returned_code = str(row.get("code") or "")
        if returned_code != futu_code:
            raise MarketDataFreshnessError(
                f"Futu snapshot code mismatch: requested={futu_code}, returned={returned_code}"
            )
        last_price = _finite_float(row.get("last_price"), "snapshot last_price")
        if last_price <= 0:
            raise MarketDataFreshnessError(
                f"Futu snapshot last_price is not positive: {last_price}"
            )
        market_state_key = "market_hk" if market == "HK" else "market_us"
        snapshot_freshness = evaluate_snapshot_freshness(
            update_time=row.get("update_time"),
            market=market,
            market_state=str(global_state.get(market_state_key) or "UNKNOWN"),
            queried_at=queried_at,
            config=self.config,
        )
        expected_session = expected_completed_session(
            trading_days,
            market,
            queried_at=queried_at,
            config=self.config,
        )
        volume = _finite_float(row.get("volume", 0), "snapshot volume")

        return {
            "source": "FUTU_OPEND_LIVE",
            "freshness_status": "FRESH",
            "request_id": request_id,
            "requested_at": started_at.isoformat(),
            "queried_at": queried_at.isoformat(),
            "round_trip_seconds": round((queried_at - started_at).total_seconds(), 3),
            "host": self.host,
            "port": self.port,
            "cache_used": False,
            "fallback_used": False,
            "program_status": program_status,
            "quote_logged_in": True,
            "opend_server_time": server_time.isoformat(),
            "opend_clock_skew_seconds": round(clock_skew, 3),
            "expected_completed_session": expected_session,
            "calendar_source": "FUTU_REQUEST_TRADING_DAYS",
            "snapshot": {
                "futu_code": futu_code,
                "last_price": round(last_price, 6),
                "volume": round(volume, 6),
                "security_status": _clean_text(row.get("sec_status")),
                **snapshot_freshness,
            },
        }


def filter_completed_daily_bars(
    df: pd.DataFrame,
    market: str,
    *,
    now: datetime | None = None,
    config: NorthstarD1Config | None = None,
    expected_session: str | None = None,
) -> pd.DataFrame:
    """Keep only completed bars, preferably using Futu's expected session."""

    if df is None or len(df) == 0:
        raise ValueError("Futu returned no daily bars")
    if "date" not in df.columns:
        raise ValueError("daily bars do not contain a date column")

    cfg = config or NorthstarD1Config()
    market = market.upper()
    if market not in MARKET_TIMEZONES:
        raise ValueError(f"unsupported market: {market}")
    timezone = MARKET_TIMEZONES[market]
    if now is None:
        local_now = datetime.now(timezone)
    elif now.tzinfo is None:
        local_now = now.replace(tzinfo=timezone)
    else:
        local_now = now.astimezone(timezone)

    result = df.copy()
    parsed_dates = pd.to_datetime(result["date"], errors="raise").dt.date
    if not parsed_dates.is_monotonic_increasing:
        order = pd.to_datetime(result["date"], errors="raise").argsort()
        result = result.iloc[order].reset_index(drop=True)
        parsed_dates = pd.to_datetime(result["date"], errors="raise").dt.date

    last_date = parsed_dates.iloc[-1]
    if last_date > local_now.date():
        raise ValueError(
            f"future daily bar detected: {last_date} > {local_now.date()} ({market})"
        )

    cutoff = time(cfg.session_close_hour, cfg.session_close_minute)
    if expected_session is not None:
        expected_date = pd.to_datetime(expected_session, errors="raise").date()
        if expected_date > local_now.date():
            raise ValueError(
                f"future expected session detected: {expected_date} > {local_now.date()}"
            )
        completed_mask = parsed_dates <= expected_date
        incomplete_excluded = int((~completed_mask).sum())
        result = result.loc[completed_mask].copy()
        cutoff_label = "FUTU_TRADING_CALENDAR"
    else:
        incomplete_excluded = 0
        if last_date == local_now.date() and local_now.time() < cutoff:
            result = result.iloc[:-1].copy()
            incomplete_excluded = 1
        cutoff_label = cutoff.strftime("%H:%M")
    if result.empty:
        raise ValueError("no completed daily bars remain after finality filtering")

    result.attrs.update(getattr(df, "attrs", {}))
    result.attrs["bar_confirmed"] = True
    result.attrs["bar_timezone"] = str(timezone.key)
    result.attrs["session_cutoff"] = cutoff_label
    result.attrs["expected_completed_session"] = expected_session
    result.attrs["incomplete_bars_excluded"] = incomplete_excluded
    result.attrs["signal_asof"] = str(
        pd.to_datetime(result["date"].iloc[-1]).date()
    )
    return result.reset_index(drop=True)


class CompletedDailyDataSource:
    """Read-only Futu source; every fetch is live, uncached and fail-closed."""

    def __init__(
        self,
        adapter: FutuAdapter | None = None,
        universe: UniverseManager | None = None,
        config: NorthstarD1Config | None = None,
        freshness_probe: FutuLiveFreshnessProbe | None = None,
    ):
        self.adapter = adapter or FutuAdapter()
        self.universe = universe or UniverseManager()
        self.config = config or NorthstarD1Config()
        self.freshness_probe = freshness_probe or FutuLiveFreshnessProbe(
            host=self.adapter.host,
            port=self.adapter.port,
            config=self.config,
        )
        self.live_contract_enforced = (
            isinstance(self.adapter, FutuAdapter)
            and isinstance(self.freshness_probe, FutuLiveFreshnessProbe)
        )

    def symbols(self, market: str, requested: Iterable[str] | None = None) -> List[str]:
        available = self.universe.get_symbols_by_market(market.upper())
        if requested is None:
            return available
        requested_set = set(requested)
        unknown = sorted(requested_set - set(available))
        if unknown:
            raise ValueError(f"symbols are outside the {market.upper()} universe: {unknown}")
        return [symbol for symbol in available if symbol in requested_set]

    def fetch(
        self,
        symbol: str,
        *,
        count: int = 320,
        now: datetime | None = None,
    ) -> pd.DataFrame:
        if now is not None:
            raise ValueError(
                "injected clocks are disabled for live scans; use "
                "filter_completed_daily_bars directly in finality tests"
            )
        info = self.universe.get_info(symbol)
        if not info:
            raise ValueError(f"symbol is not in UniverseManager: {symbol}")
        market = info["market"].upper()
        if market not in ("HK", "US"):
            raise ValueError(f"Northstar-D1 supports only HK/US: {symbol}")

        # Preflight OpenD with a short TCP timeout before the SDK's K-line call.
        # This also obtains the uncached snapshot and Futu trading calendar.
        audit = self.freshness_probe.query(symbol, market)
        bars = self.adapter.fetch_kline(
            symbol,
            count=count,
            ktype="K_DAY",
            autype="qfq",
        )
        if bars is None:
            raise MarketDataFreshnessError(f"Futu QFQ daily data unavailable: {symbol}")

        pipeline_completed_at = datetime.now(timezone.utc)
        seconds_after_snapshot = (
            pipeline_completed_at - datetime.fromisoformat(audit["queried_at"])
        ).total_seconds()
        if seconds_after_snapshot > self.config.snapshot_max_age_seconds:
            raise MarketDataFreshnessError(
                f"Futu fetch pipeline exceeded freshness window: "
                f"{seconds_after_snapshot:.1f}s > "
                f"{self.config.snapshot_max_age_seconds}s"
            )
        audit["snapshot"].update(
            evaluate_snapshot_freshness(
                update_time=audit["snapshot"]["update_time"],
                market=market,
                market_state=audit["snapshot"]["market_state"],
                queried_at=pipeline_completed_at,
                config=self.config,
            )
        )
        audit["pipeline_completed_at"] = pipeline_completed_at.isoformat()
        audit["seconds_after_snapshot"] = round(seconds_after_snapshot, 3)

        filtered = filter_completed_daily_bars(
            bars,
            market,
            now=pipeline_completed_at,
            config=self.config,
            expected_session=audit["expected_completed_session"],
        )
        actual_session = str(pd.to_datetime(filtered["date"].iloc[-1]).date())
        expected_session = audit["expected_completed_session"]
        if actual_session != expected_session:
            raise MarketDataFreshnessError(
                f"Futu completed daily bar mismatch for {symbol}: "
                f"expected={expected_session}, actual={actual_session}"
            )

        filtered.attrs.update(
            {
                "source": "FUTU_OPEND_LIVE",
                "freshness_status": "FRESH",
                "request_id": audit["request_id"],
                "queried_at": audit["queried_at"],
                "cache_used": False,
                "fallback_used": False,
                "expected_completed_session": expected_session,
                "kline_query": "FUTU_REQUEST_HISTORY_KLINE_QFQ",
                "market_data": audit,
            }
        )
        return filtered
