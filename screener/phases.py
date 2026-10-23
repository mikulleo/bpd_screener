"""Weinstein-style phase classification on weekly bars.

Three lenses, as in the team's phase notes:
  * trend:    10W EMA vs 30W EMA (and their slopes)
  * RS:       ticker:$SPX ratio vs its own 52W EMA (leader above, laggard below)
  * squeeze:  weekly BB Width(20,2) position inside CHAN(125)

The base phase (1 / 2a / 2b / 3 / 4) is decided by a weekly state machine, because
it depends on what came before: 2a is the first uptrend after a phase 1 base (or a
phase 4 decline), 2b is an uptrend that resumes after a phase 3 pause, 3 is a pause
after an uptrend, 1 is a pause after a decline. Phase 2 is then refined with RS and
the BBW squeeze into 2b+RS+, 2b+RS-, 2b-AB, 2b, 2a+ and 2a.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import bb_width, channel_position, price_channel

# Display order, best setups first.
PHASES = ["2b+RS+", "2b+RS-", "2a+", "2b-AB", "2b", "2a", "3", "1", "4"]

PHASE_HELP = {
    "2b+RS+": "Uptrend resumed after a phase 3 pause, weekly BBW at/near channel low, RS leading",
    "2b+RS-": "Uptrend resumed after a phase 3 pause, weekly BBW at/near channel low, RS lagging",
    "2a+": "First uptrend out of a phase 1 base, RS leading",
    "2b-AB": "Uptrend after a phase 3 pause, weekly BBW below half of its channel but not at the low",
    "2b": "Uptrend after a phase 3 pause, weekly BBW above half of its channel (no squeeze)",
    "2a": "First uptrend out of a phase 1 base, RS not leading",
    "3": "Pause after an uptrend: 10W EMA rolling over / around 30W EMA",
    "1": "Base after a decline: 10W EMA below or wiggling around 30W EMA",
    "4": "Downtrend: 10W EMA below 30W EMA, both falling, price below 30W EMA",
}


@dataclass
class PhaseParams:
    # Defaults fitted to the team's dated phase calls (notes like "20/7 LM: ...") in WL 2026.xlsx.
    ema_margin_pct: float = 0.5       # 3 -> 2b: 10W back above 30W EMA by at least this %
    exit_spread_pct: float = 1.5      # 2 -> 3: 10W this % below 30W EMA ...
    confirm_weeks: int = 2            # ... for this many weeks in a row
    early_2a_spread_pct: float = -1.0  # 1 -> 2a already when 10W is within this % of 30W ...
    early_2a_slope_pct: float = 1.0   # ... and 10W rising at least this % over 3 weeks, price above 30W
    mature_2a_weeks: int = 8          # 2a becomes 2b once the uptrend has lasted this long
    down_weeks: int = 8               # 3/1 -> 4 needs a downtrend persisting this many weeks
    four_exit_weeks: int = 3          # 4 -> 1 once the decline has stalled for this many weeks
    rs_lead_pct: float = 8.0          # RS+ = RS line at least this % above its 52W EMA
    bbw_near_low: float = 0.15        # 2b+ = weekly BBW channel position (min of last N weeks) <= this
    bbw_half: float = 0.50            # 2b-AB if the current position <= this, else 2b
    bbw_lookback_weeks: int = 3
    slope_weeks: int = 3
    rs_ema_weeks: int = 52


def weekly_features(weekly: pd.DataFrame, bench_weekly: pd.Series, p: PhaseParams) -> pd.DataFrame:
    close = weekly["Close"]
    f = pd.DataFrame(index=weekly.index)
    f["close"] = close
    f["ema10"] = close.ewm(span=10, adjust=False).mean()
    f["ema30"] = close.ewm(span=30, adjust=False).mean()
    f["spread"] = (f["ema10"] / f["ema30"] - 1) * 100
    f["slope10"] = (f["ema10"] / f["ema10"].shift(p.slope_weeks) - 1) * 100
    f["slope30"] = (f["ema30"] / f["ema30"].shift(p.slope_weeks) - 1) * 100
    bench = bench_weekly.reindex(weekly.index).ffill()
    f["rs"] = close / bench
    f["rs_ema"] = f["rs"].ewm(span=p.rs_ema_weeks, adjust=False).mean()
    f["rs_margin"] = (f["rs"] / f["rs_ema"] - 1) * 100
    f["rs_ema_slope"] = (f["rs_ema"] / f["rs_ema"].shift(4) - 1) * 100
    f["rs_ema10"] = f["rs"].ewm(span=10, adjust=False).mean()
    f["high10"] = weekly["High"].rolling(10, min_periods=1).max()
    bbw = bb_width(close)
    hi, lo = price_channel(bbw, 125)
    f["bbw"] = bbw
    f["bbw_hi"] = hi
    f["bbw_lo"] = lo
    f["bbw_pos"] = channel_position(bbw, hi, lo)
    f["bbw_pos_min"] = f["bbw_pos"].rolling(p.bbw_lookback_weeks, min_periods=1).min()
    return f


def base_phase_history(f: pd.DataFrame, p: PhaseParams) -> pd.Series:
    """Run the weekly state machine; returns 1/2a/2b/3/4 per week ('' during warm-up).

    2a -> 2b   uptrend established (mature_2a_weeks) - no phase 3 needed in between
    2x -> 3    10W EMA drops clearly below 30W EMA (exit_spread_pct, confirm_weeks)
    2a -> 1    a fresh uptrend that fails goes back to its base
    3  -> 2b   10W EMA back above 30W EMA and rising
    3  -> 4    10W below 30W for down_weeks and still in a rolling downtrend
    4  -> 1    decline stalls (four_exit_weeks)
    1  -> 2a   10W EMA turning up through/near the 30W EMA, price above it (early 2a)
    1  -> 4    rolling downtrend resumes for down_weeks in a row
    """
    states = []
    state, age = "", 0
    below = exit_run = down_run = calm_run = 0
    for i, r in enumerate(f.itertuples()):
        if i < 30 or np.isnan(r.slope30):
            states.append("")
            continue
        down = r.spread < -p.ema_margin_pct and r.slope10 < 0 and r.slope30 < 0 and r.close < r.ema30
        below = below + 1 if r.spread < 0 else 0
        exit_run = exit_run + 1 if r.spread < -p.exit_spread_pct else 0
        down_run = down_run + 1 if down else 0
        calm_run = 0 if down else calm_run + 1
        up_again = r.spread > p.ema_margin_pct and r.slope10 > 0
        early_up = r.spread > p.early_2a_spread_pct and r.slope10 > p.early_2a_slope_pct and r.close > r.ema30

        prev = state
        if not state:
            state = "2b" if r.spread > p.ema_margin_pct else ("4" if down else "1")
        elif state in ("2a", "2b"):
            if exit_run >= p.confirm_weeks:
                state = "1" if state == "2a" and age < p.mature_2a_weeks else "3"
            elif state == "2a" and age >= p.mature_2a_weeks:
                state = "2b"
        elif state == "3":
            if up_again:
                state = "2b"
            elif down and below >= p.down_weeks:
                state = "4"
        elif state == "4":
            if calm_run >= p.four_exit_weeks:
                state = "1"
        elif state == "1":
            if early_up:
                state = "2a"
            elif down_run >= p.down_weeks:
                state = "4"
        age = age + 1 if state == prev else 1
        states.append(state)
    return pd.Series(states, index=f.index)


def refine(base: str, rs_margin: float, rs_rising: bool, bbw_pos: float, bbw_pos_min: float, p: PhaseParams) -> str:
    if base == "2a":
        # "better RS": RS line above its 52W EMA and that EMA itself rising
        return "2a+" if rs_margin > 0 and rs_rising else "2a"
    if base == "2b":
        if not np.isnan(bbw_pos_min) and bbw_pos_min <= p.bbw_near_low:
            return "2b+RS+" if rs_margin >= p.rs_lead_pct else "2b+RS-"
        if not np.isnan(bbw_pos) and bbw_pos <= p.bbw_half:
            return "2b-AB"
        return "2b"
    return base


def classify(weekly: pd.DataFrame, bench_weekly: pd.Series, p: PhaseParams | None = None,
             features: pd.DataFrame | None = None) -> dict:
    p = p or PhaseParams()
    f = features if features is not None else weekly_features(weekly, bench_weekly, p)
    base = base_phase_history(f, p)
    if not base.iloc[-1]:
        return {"phase": None, "reason": "Not enough weekly history (needs ~30 weeks)", "flags": [], "doubts": [], "history": []}

    phases = [
        refine(b, r, sl > 0, pos, pmin, p) if b else ""
        for b, r, sl, pos, pmin in zip(base, f["rs_margin"].fillna(0), f["rs_ema_slope"].fillna(0), f["bbw_pos"], f["bbw_pos_min"])
    ]
    phase_s = pd.Series(phases, index=f.index)
    last = f.iloc[-1]
    phase = phases[-1]

    # how long in the current base phase, and what came before it
    b_now = base.iloc[-1]
    run = 1
    while run < len(base) and base.iloc[-1 - run] == b_now:
        run += 1
    prior = base.iloc[-1 - run] if run < len(base) else ""

    # flags = things to watch; doubts = the call sits on a threshold -> worth a manual look
    flags, doubts = [], []
    if b_now in ("2a", "2b"):
        if last.spread < 0:
            flags.append(f"10W EMA dipped {last.spread:.1f}% below 30W EMA - becomes 3 below -{p.exit_spread_pct:g}%")
            if last.spread < -(p.exit_spread_pct - 0.5):
                doubts.append(f"10W EMA {last.spread:.1f}% below 30W - right at the phase 3 line")
        if last.slope10 < 0:
            flags.append("10W EMA turning down")
        if last.close < last.ema30:
            flags.append("price below 30W EMA")
        if last.rs < last.rs_ema10:
            flags.append("RS line below its 10W EMA")
        if not np.isnan(last.bbw_pos) and last.bbw_pos > 0.7 and last.close < 0.92 * last.high10:
            flags.append("getting loose: weekly volatility expanding without new highs (watch for 3)")
        if b_now == "2b":
            if not np.isnan(last.bbw_pos_min) and abs(last.bbw_pos_min - p.bbw_near_low) < 0.03:
                doubts.append(f"weekly BBW low {last.bbw_pos_min:.0%} is right at the 2b+ line ({p.bbw_near_low:.0%})")
            elif not np.isnan(last.bbw_pos) and last.bbw_pos_min > p.bbw_near_low and abs(last.bbw_pos - p.bbw_half) < 0.03:
                doubts.append(f"weekly BBW {last.bbw_pos:.0%} is right at the half-channel line")
            if phase.startswith("2b+") and abs(last.rs_margin - p.rs_lead_pct) < 1.5:
                doubts.append(f"RS {last.rs_margin:+.1f}% vs its 52W EMA is at the RS+ line ({p.rs_lead_pct:g}%)")
    if b_now == "3":
        if last.spread > 0 and last.slope10 > 0:
            flags.append("10W EMA turning back up - watch for 2b")
        elif last.spread < 0 and last.slope30 < 0 and last.close < last.ema30:
            flags.append("rolling over: could become phase 4")
    if b_now == "4" and last.slope10 > 0:
        flags.append("10W EMA turning up - decline may be ending (phase 1)")
    if b_now == "1":
        if last.slope10 > 0 and last.close > last.ema30:
            flags.append("10W EMA rising, price above 30W EMA - watch for 2a")
    if b_now in ("1", "3") and run >= 26:
        flags.append(f"{run} weeks in phase {b_now} - long sideways range; 1 vs 3 is often clear only in hindsight")

    weeks_hist = 52
    hist = phase_s.iloc[-weeks_hist:]
    return {
        "phase": phase,
        "base_phase": b_now,
        "weeks_in_phase": int(run),
        "prior_phase": prior or None,
        "rs_lead": bool(last.rs_margin >= p.rs_lead_pct),
        "rs_margin_pct": round(float(last.rs_margin), 2),
        "rs_ema_rising": bool(last.rs_ema_slope > 0),
        "ema_spread_pct": round(float(last.spread), 2),
        "ema10_slope_pct": round(float(last.slope10), 2),
        "ema30_slope_pct": round(float(last.slope30), 2),
        "bbw_pos": None if np.isnan(last.bbw_pos) else round(float(last.bbw_pos), 3),
        "bbw_pos_min": None if np.isnan(last.bbw_pos_min) else round(float(last.bbw_pos_min), 3),
        "flags": flags,
        "doubts": doubts,
        "confident": not doubts,
        "history": [{"week": d.strftime("%Y-%m-%d"), "phase": ph} for d, ph in hist.items()],
    }
