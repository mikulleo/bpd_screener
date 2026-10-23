"""Market data from Yahoo Finance (via yfinance), with a small in-process TTL cache.

On Railway the process is long-lived, so the cache saves repeat downloads. On Vercel
it only helps while a function instance stays warm.
"""

import logging
import threading
import time
import warnings
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore", module=r"yfinance(\..*)?")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

BENCHMARK = "^GSPC"
CACHE_TTL_SECONDS = 30 * 60

_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


class DataError(Exception):
    pass


@dataclass
class PriceData:
    daily: pd.DataFrame
    instrument_type: str | None
    name: str | None
    currency: str | None


@dataclass
class EarningsData:
    # Reported quarterly EPS, newest first: list of (date, eps)
    quarters: list[tuple[pd.Timestamp, float]]
    source: str


def _cached(key: str, loader):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
    value = loader()
    with _lock:
        _cache[key] = (now, value)
    return value


def _clean_index(df: pd.DataFrame) -> pd.DataFrame:
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df = df.copy()
    df.index = idx.normalize()
    return df[~df.index.duplicated(keep="last")]


def get_prices(symbol: str) -> PriceData:
    def load():
        t = yf.Ticker(symbol)
        try:
            hist = t.history(period="5y", auto_adjust=True)
        except Exception as e:  # yfinance raises a mix of its own and requests errors
            raise DataError(_describe(e)) from e
        if hist is None or hist.empty:
            raise DataError("No price data (unknown ticker or delisted?)")
        meta = t.history_metadata or {}
        daily = _clean_index(hist[["Open", "High", "Low", "Close", "Volume"]]).dropna(subset=["Close"])
        return PriceData(
            daily=daily,
            instrument_type=meta.get("instrumentType"),
            name=meta.get("longName") or meta.get("shortName"),
            currency=meta.get("currency"),
        )

    return _cached(f"px:{symbol}", load)


def get_benchmark() -> pd.Series:
    return get_prices(BENCHMARK).daily["Close"]


def get_earnings(symbol: str) -> EarningsData:
    def load():
        t = yf.Ticker(symbol)
        quarters: list[tuple[pd.Timestamp, float]] = []
        try:
            ed = t.get_earnings_dates(limit=16)
        except Exception:
            ed = None
        if ed is not None and not ed.empty and "Reported EPS" in ed.columns:
            rep = _clean_index(ed)["Reported EPS"].dropna().sort_index(ascending=False)
            quarters = [(d, float(v)) for d, v in rep.items()]
        if len(quarters) >= 5:
            return EarningsData(quarters, "Yahoo earnings calendar (adjusted EPS)")
        try:
            qis = t.quarterly_income_stmt
        except Exception:
            qis = None
        if qis is not None and not qis.empty:
            row = next((r for r in ("Diluted EPS", "Basic EPS") if r in qis.index), None)
            if row:
                s = qis.loc[row].dropna()
                s.index = pd.to_datetime(s.index)
                stmt = [(d, float(v)) for d, v in s.sort_index(ascending=False).items()]
                if len(stmt) > len(quarters):
                    return EarningsData(stmt, "Quarterly income statement (GAAP diluted EPS)")
        return EarningsData(quarters, "Yahoo earnings calendar (adjusted EPS)")

    return _cached(f"eps:{symbol}", load)


def _describe(e: Exception) -> str:
    name = type(e).__name__
    if "RateLimit" in name or "Too Many Requests" in str(e):
        return "Yahoo Finance rate limit hit - wait a minute and retry"
    return f"{name}: {e}"
