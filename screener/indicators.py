"""Indicator math, matching StockCharts definitions where one exists."""

import numpy as np
import pandas as pd


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV into Friday-ending weekly bars (the current week may be partial)."""
    w = daily.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    )
    return w.dropna(subset=["Close"])


def bb_width(close: pd.Series, period: int = 20, mult: float = 2.0) -> pd.Series:
    """BB Width = (upper - lower) / middle * 100, population std dev like StockCharts."""
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    return (2 * mult * sd) / mid * 100


def price_channel(series: pd.Series, period: int = 125) -> tuple[pd.Series, pd.Series]:
    """StockCharts Price Channel: highest/lowest of the prior `period` bars (current bar excluded)."""
    prior = series.shift(1)
    return prior.rolling(period).max(), prior.rolling(period).min()


def channel_position(series: pd.Series, hi: pd.Series, lo: pd.Series) -> pd.Series:
    """0 = at the channel low, 1 = at the channel high; below 0 means a fresh low."""
    span = (hi - lo).replace(0, np.nan)
    return (series - lo) / span


def relative_strength(close: pd.Series, bench: pd.Series) -> pd.Series:
    """$SYMBOL:$SPX ratio on common dates."""
    joined = pd.concat([close, bench], axis=1, join="inner").dropna()
    return joined.iloc[:, 0] / joined.iloc[:, 1]


def zigzag_pullbacks(high: np.ndarray, low: np.ndarray, threshold: float) -> list[dict]:
    """Peak-to-trough pullbacks deeper than `threshold` (fraction), oldest first.

    The series is assumed to start at a peak (the base pivot). The final, still-open
    pullback is always included even when it is shallower than the threshold, so the
    tightness at the right side of the base is visible.
    """
    pullbacks = []
    n = len(high)
    if n == 0:
        return pullbacks
    peak_i, peak = 0, high[0]
    trough_i, trough = None, None
    for i in range(1, n):
        if trough is None:
            if high[i] > peak:
                peak_i, peak = i, high[i]
            elif (peak - low[i]) / peak >= threshold:
                trough_i, trough = i, low[i]
        else:
            if low[i] < trough:
                trough_i, trough = i, low[i]
            elif (high[i] - trough) / trough >= threshold:
                pullbacks.append({"peak": peak_i, "trough": trough_i, "depth": (peak - trough) / peak})
                peak_i, peak = i, high[i]
                trough_i, trough = None, None
    if trough is not None:
        pullbacks.append({"peak": peak_i, "trough": trough_i, "depth": (peak - trough) / peak, "open": True})
    elif peak_i < n - 1:
        j = peak_i + 1 + int(np.argmin(low[peak_i + 1:]))
        depth = (peak - low[j]) / peak
        if depth > 0:
            pullbacks.append({"peak": peak_i, "trough": j, "depth": depth, "open": True})
    return pullbacks


def detect_base(weekly: pd.DataFrame, max_depth: float, grace: int, lookback: int = 104) -> dict | None:
    """Find the latest base on weekly bars.

    The base pivot is the highest high of the longest look-back window whose
    correction (pivot high to lowest low since) stays within `max_depth`. The last
    `grace` weeks are excluded from the pivot search so a fresh breakout still shows
    the base it came out of. Depth only grows as the window extends, so the scan stops
    at the first window that is too deep.
    """
    w = weekly.iloc[-lookback:]
    n = len(w)
    if n <= grace + 1:
        return None
    highs, lows = w["High"].to_numpy(), w["Low"].to_numpy()
    end = n - grace
    pivot = None
    for k in range(end - 1, -1, -1):
        i = k + int(np.argmax(highs[k:end]))
        depth = (highs[i] - lows[i:].min()) / highs[i]
        if depth > max_depth:
            break
        pivot = i
    if pivot is None:
        return None
    pivot_high = highs[pivot]
    base_low = lows[pivot:].min()
    return {
        "start": w.index[pivot],
        "weeks": n - pivot,
        "pivot_high": float(pivot_high),
        "low": float(base_low),
        "depth": float((pivot_high - base_low) / pivot_high),
    }


def volume_above_price(close: pd.Series, volume: pd.Series, price: float) -> float:
    """Share of volume (by closing price, like StockCharts VbP) traded above `price`."""
    total = volume.sum()
    if total <= 0:
        return float("nan")
    return float(volume[close > price].sum() / total)


def volume_profile(close: pd.Series, volume: pd.Series, bins: int = 16) -> dict:
    """Volume-by-price histogram for charting."""
    lo, hi = float(close.min()), float(close.max())
    if hi <= lo:
        return {"edges": [lo, hi], "volume": [float(volume.sum())]}
    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.digitize(close.to_numpy(), edges) - 1, 0, bins - 1)
    vols = np.bincount(idx, weights=volume.to_numpy(dtype=float), minlength=bins)
    return {"edges": [round(float(e), 4) for e in edges], "volume": [float(v) for v in vols]}
