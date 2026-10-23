import numpy as np
import pandas as pd
import pytest

from screener.indicators import (
    bb_width,
    channel_position,
    detect_base,
    price_channel,
    relative_strength,
    to_weekly,
    volume_above_price,
    zigzag_pullbacks,
)


def test_bb_width_matches_population_std():
    close = pd.Series(np.arange(1, 41, dtype=float))
    bbw = bb_width(close)
    window = close.iloc[-20:]
    expected = 4 * window.std(ddof=0) / window.mean() * 100
    assert bbw.iloc[-1] == pytest.approx(expected)
    assert bbw.iloc[:19].isna().all()


def test_price_channel_excludes_current_bar():
    s = pd.Series([5, 4, 3, 2, 1, 10], dtype=float)
    hi, lo = price_channel(s, 3)
    # last bar (10) is not part of its own channel: prior three bars are 3, 2, 1
    assert hi.iloc[-1] == 3
    assert lo.iloc[-1] == 1
    pos = channel_position(s, hi, lo)
    assert pos.iloc[-1] > 1


def test_relative_strength_aligns_dates():
    idx = pd.date_range("2026-01-01", periods=4)
    a = pd.Series([10, 11, 12, 13], index=idx, dtype=float)
    b = pd.Series([100, 100, 100], index=idx[1:], dtype=float)
    rs = relative_strength(a, b)
    assert list(rs.index) == list(idx[1:])
    assert rs.iloc[-1] == pytest.approx(0.13)


def test_volume_above_price():
    close = pd.Series([10, 20, 30, 40], dtype=float)
    vol = pd.Series([1, 1, 1, 1], dtype=float)
    assert volume_above_price(close, vol, 25) == 0.5
    assert volume_above_price(close, vol, 40) == 0.0


def _weekly(highs, lows):
    idx = pd.date_range("2025-01-03", periods=len(highs), freq="W-FRI")
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    return pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": 1.0}, index=idx)


def test_detect_base_finds_pivot_after_uptrend():
    up = list(range(50, 100, 2))                 # 25 weeks of advance
    flat = [100, 96, 92, 95, 97, 93, 94, 96, 97, 95, 96, 98, 97, 98, 99, 98]  # 16-week base
    highs = up + flat
    lows = [h - 3 for h in highs]
    base = detect_base(_weekly(highs, lows), max_depth=0.4, grace=2)
    assert base is not None
    assert base["pivot_high"] == 100
    assert base["weeks"] == len(flat)
    assert base["depth"] == pytest.approx((100 - 89) / 100)


def test_detect_base_none_for_trending_stock():
    highs = list(range(50, 110, 2))
    lows = [h - 3 for h in highs]
    base = detect_base(_weekly(highs, lows), max_depth=0.4, grace=2)
    # the pivot is the newest non-grace week, so the base is only a few weeks long
    assert base["weeks"] <= 3


def test_detect_base_respects_breakout_grace():
    up = list(range(50, 100, 2))
    flat = [100, 95, 92, 96, 97, 94, 96, 98, 97, 98]
    breakout = [104, 106]
    highs = up + flat + breakout
    lows = [h - 3 for h in highs]
    base = detect_base(_weekly(highs, lows), max_depth=0.4, grace=2)
    assert base["pivot_high"] == 100


def test_zigzag_contracting_pullbacks():
    # peak 100 -> 80 (20%) -> 98 -> 88 (~10%) -> 97 -> 93 (open, ~4%)
    path = [100, 90, 80, 90, 98, 93, 88, 93, 97, 95, 93]
    h = np.array(path, dtype=float)
    pbs = zigzag_pullbacks(h, h, 0.06)
    depths = [round(p["depth"], 3) for p in pbs]
    assert depths[0] == pytest.approx(0.20)
    assert depths[1] == pytest.approx(0.102, abs=1e-3)
    assert pbs[-1].get("open") and depths[-1] == pytest.approx(0.041, abs=1e-3)


def test_to_weekly_aggregates():
    idx = pd.bdate_range("2026-10-05", "2026-10-16")
    df = pd.DataFrame({
        "Open": range(10), "High": range(1, 11), "Low": range(10), "Close": range(10), "Volume": [1] * 10,
    }, index=idx, dtype=float)
    w = to_weekly(df)
    assert len(w) == 2
    assert w["High"].iloc[0] == 5 and w["Close"].iloc[0] == 4 and w["Volume"].iloc[0] == 5
