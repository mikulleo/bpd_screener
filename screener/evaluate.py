"""Turn price + earnings data into per-criterion verdicts for one ticker."""

import math

import numpy as np
import pandas as pd

from . import data as md
from .config import Thresholds
from .phases import classify, weekly_features
from .indicators import (
    bb_width,
    channel_position,
    detect_base,
    price_channel,
    relative_strength,
    to_weekly,
    volume_above_price,
    volume_profile,
    zigzag_pullbacks,
)

PASS, WARN, FAIL, NA = "pass", "warn", "fail", "na"

# Weight of each criterion in the ranking score.
WEIGHTS = {
    "weekly_bbw": 1.0,
    "daily_bbw": 1.0,
    "rs": 1.25,
    "base": 1.0,
    "contraction": 1.0,
    "overhead": 1.0,
    "eps": 1.0,
}

CRITERIA_LABELS = {
    "weekly_bbw": "Weekly BBW",
    "daily_bbw": "Daily BBW",
    "rs": "RS vs SPX",
    "base": "Base length",
    "contraction": "Contraction",
    "overhead": "Overhead",
    "eps": "EPS",
}


def _grade_low(value: float, pass_at: float, warn_at: float) -> str:
    """Lower is better."""
    if value is None or math.isnan(value):
        return NA
    return PASS if value <= pass_at else WARN if value <= warn_at else FAIL


def _grade_high(value: float, pass_at: float, warn_at: float) -> str:
    """Higher is better."""
    if value is None or math.isnan(value):
        return NA
    return PASS if value >= pass_at else WARN if value >= warn_at else FAIL


def _crit(status: str, value: str, detail: str, **metrics) -> dict:
    return {"status": status, "value": value, "detail": detail, "metrics": metrics}


def _r(x, nd=4):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def _series(s: pd.Series, nd=4) -> list:
    return [_r(v, nd) for v in s.to_numpy()]


def stockcharts_symbol(symbol: str) -> str:
    return symbol.replace("-", "/") if "-" in symbol else symbol


def evaluate_symbol(symbol: str, th: Thresholds) -> dict:
    result = {
        "symbol": symbol,
        "stockcharts_url": f"https://stockcharts.com/h-sc/ui?s={stockcharts_symbol(symbol)}",
        "criteria": {},
    }
    try:
        px = md.get_prices(symbol)
        bench = md.get_benchmark()
    except md.DataError as e:
        result.update(verdict="error", error=str(e), score=None)
        return result

    daily = px.daily
    result["name"] = px.name
    result["instrument_type"] = px.instrument_type
    result["last_date"] = daily.index[-1].strftime("%Y-%m-%d")
    result["last_close"] = _r(daily["Close"].iloc[-1], 2)

    if len(daily) < 60:
        result.update(verdict="error", error=f"Only {len(daily)} sessions of history - too short to evaluate", score=None)
        return result

    weekly = to_weekly(daily)
    close = float(daily["Close"].iloc[-1])
    crit = result["criteria"]

    # --- Phase -------------------------------------------------------------------
    bench_w = bench.resample("W-FRI").last().dropna()
    pp = th.phase_params()
    pf = weekly_features(weekly, bench_w, pp)
    result["phase"] = classify(weekly, bench_w, pp, features=pf)

    # --- Base --------------------------------------------------------------------
    base = detect_base(weekly, th.base_max_depth_pct / 100, th.base_breakout_grace_weeks)
    base_weeks = base["weeks"] if base else 0
    if base:
        dist_pivot = close / base["pivot_high"] - 1
        status = _grade_high(base_weeks, th.base_pass_weeks, th.base_warn_weeks)
        if dist_pivot > 0.05:
            position = f"extended {dist_pivot:+.1%} above pivot"
        elif dist_pivot > 0:
            position = f"breaking out ({dist_pivot:+.1%} vs pivot)"
        else:
            position = f"{dist_pivot:.1%} below pivot"
        crit["base"] = _crit(
            status,
            f"{base_weeks} wks",
            f"Base since {base['start']:%Y-%m-%d}, depth {base['depth']:.0%}, pivot {base['pivot_high']:.2f}; {position}",
            weeks=base_weeks,
            depth=_r(base["depth"]),
            pivot=_r(base["pivot_high"], 2),
            dist_pivot=_r(dist_pivot),
        )
        result["pivot"] = _r(base["pivot_high"], 2)
        result["dist_pivot"] = _r(dist_pivot)
    else:
        crit["base"] = _crit(FAIL, "none", "No base found: price is trending or the correction is deeper than the max depth")

    # --- Weekly BBW --------------------------------------------------------------
    wk_bbw = bb_width(weekly["Close"])
    wk_hi, wk_lo = price_channel(wk_bbw, 125)
    wk_pos = channel_position(wk_bbw, wk_hi, wk_lo)
    look = int(min(max(base_weeks, th.weekly_bbw_min_weeks), th.weekly_bbw_max_weeks))
    recent = wk_pos.iloc[-look:].dropna()
    if recent.empty:
        crit["weekly_bbw"] = _crit(NA, "n/a", "Not enough weekly history for BBW(20) with CHAN(125)")
    else:
        min_pos = float(recent.min())
        when = recent.idxmin()
        crit["weekly_bbw"] = _crit(
            _grade_low(min_pos, th.weekly_bbw_pass, th.weekly_bbw_warn),
            f"{min_pos:.0%}",
            f"Lowest BBW position in channel over last {look} wks: {min_pos:.0%} (week of {when:%Y-%m-%d}); now {float(wk_pos.iloc[-1]):.0%}",
            min_pos=_r(min_pos),
            now=_r(wk_pos.iloc[-1]),
            weeks=look,
        )

    # --- Daily BBW ---------------------------------------------------------------
    d_bbw = bb_width(daily["Close"])
    d_hi, d_lo = price_channel(d_bbw, 125)
    d_pos = channel_position(d_bbw, d_hi, d_lo)
    d_recent = d_pos.iloc[-th.daily_bbw_days:].dropna()
    if d_recent.empty:
        crit["daily_bbw"] = _crit(NA, "n/a", "Not enough daily history")
    else:
        share = float((d_recent <= 0.5).mean())
        d_min = float(d_recent.min())
        status = _grade_high(share, th.daily_bbw_pass, th.daily_bbw_warn)
        ideal = d_min <= th.daily_bbw_ideal
        crit["daily_bbw"] = _crit(
            status,
            f"{share:.0%}" + (" ★" if ideal else ""),
            f"{share:.0%} of last {len(d_recent)} sessions in bottom half of channel; lowest {d_min:.0%}"
            + (" (at/near channel lows)" if ideal else "")
            + f"; now {float(d_pos.iloc[-1]):.0%}",
            share_bottom_half=_r(share),
            min_pos=_r(d_min),
            now=_r(d_pos.iloc[-1]),
            ideal=ideal,
        )

    # --- Relative strength -------------------------------------------------------
    rs = relative_strength(daily["Close"], bench)
    rs_52 = rs.iloc[-252:]
    rs_high = float(rs_52.max())
    rs_best_recent = float(rs.iloc[-5:].max())
    rs_gap = (1 - rs_best_recent / rs_high) * 100
    new_high_days = int((len(rs_52) - 1) - int(np.argmax(rs_52.to_numpy())))
    crit["rs"] = _crit(
        _grade_low(rs_gap, th.rs_pass_pct, th.rs_warn_pct),
        "new high" if rs_gap <= 0.05 else f"-{rs_gap:.1f}%",
        f"RS line {rs_gap:.1f}% below its 52-week high (high set {new_high_days} sessions ago)"
        if rs_gap > 0.05 else "RS line at a new 52-week high",
        gap_pct=_r(rs_gap, 2),
        high_days_ago=new_high_days,
    )

    # --- Volatility contraction --------------------------------------------------
    if base and base_weeks >= 4:
        seg = daily.loc[daily.index > base["start"] - pd.Timedelta(days=7)]
        rng = (seg["High"] - seg["Low"]) / seg["Close"]
        third = max(len(rng) // 3, 5)
        early = float(rng.iloc[:third].mean())
        late = float(rng.iloc[-th.contraction_recent_days:].mean())
        ratio = late / early if early > 0 else float("nan")
        # Pullbacks are counted on weekly swings; the swing size adapts to the stock's own
        # volatility so a volatile name doesn't produce dozens of "contractions".
        wseg = weekly.loc[weekly.index >= base["start"]]
        swing = max(th.pullback_zigzag_pct / 100, 1.5 * float(((wseg["High"] - wseg["Low"]) / wseg["Close"]).median()))
        pbs = zigzag_pullbacks(wseg["High"].to_numpy(), wseg["Low"].to_numpy(), swing)
        depths = [p["depth"] for p in pbs]
        status = _grade_low(ratio, th.contraction_pass, th.contraction_warn)
        note = ""
        # Daily BBW sitting near its 6-month low is itself evidence of contracting volatility.
        d_now = float(d_pos.iloc[-1]) if not math.isnan(d_pos.iloc[-1]) else float("nan")
        if status != PASS and d_now <= th.contraction_bbw_now:
            status, note = PASS, f"; daily BBW is in the bottom {th.contraction_bbw_now:.0%} of its channel"
        if status == PASS and len(depths) >= 2 and depths[-1] >= max(depths) * 0.999 and depths[-1] > 0.08:
            status, note = WARN, ", but the latest pullback is the deepest"
        seq = " → ".join(f"{d:.0%}" for d in depths) if depths else "none"
        crit["contraction"] = _crit(
            status,
            f"{ratio:.2f}×",
            f"Daily range last {th.contraction_recent_days} sessions is {ratio:.2f}× the early-base range{note}; pullbacks {seq}",
            range_ratio=_r(ratio),
            pullbacks=[_r(d) for d in depths],
        )
        result["pullbacks"] = [
            {"peak": wseg.index[p["peak"]].strftime("%Y-%m-%d"), "trough": wseg.index[p["trough"]].strftime("%Y-%m-%d"), "depth": _r(p["depth"])}
            for p in pbs
        ]
    else:
        crit["contraction"] = _crit(FAIL if not base else WARN, "n/a", "Base too short to measure contraction")

    # --- Overhead supply ---------------------------------------------------------
    vbp = daily.iloc[-th.overhead_lookback_days:]
    above = volume_above_price(vbp["Close"], vbp["Volume"], close)
    hi_2y = float(vbp["High"].max())
    hi_5y = float(daily["High"].max())
    if math.isnan(above):
        crit["overhead"] = _crit(NA, "n/a", "No volume data")
    else:
        crit["overhead"] = _crit(
            _grade_low(above * 100, th.overhead_pass_pct, th.overhead_warn_pct),
            f"{above:.0%}",
            f"{above:.0%} of 2-yr volume traded above current price; {close / hi_2y - 1:.1%} from 2-yr high, {close / hi_5y - 1:.1%} from 5-yr high",
            above_pct=_r(above * 100, 2),
            from_2y_high=_r(close / hi_2y - 1),
            from_5y_high=_r(close / hi_5y - 1),
        )

    # --- EPS ---------------------------------------------------------------------
    if px.instrument_type and px.instrument_type != "EQUITY":
        crit["eps"] = _crit(NA, "n/a", f"{px.instrument_type}: no earnings")
    else:
        crit["eps"] = _eps_criterion(symbol, th)

    # --- Verdict -----------------------------------------------------------------
    statuses = {k: v["status"] for k, v in crit.items()}
    fails = [k for k, s in statuses.items() if s == FAIL]
    warns = [k for k, s in statuses.items() if s == WARN]
    # Missing data on a criterion that should exist (equities) is a reason to look manually.
    missing = [k for k, s in statuses.items() if s == NA and not (k == "eps" and px.instrument_type not in (None, "EQUITY"))]
    if fails:
        verdict = "reject"
    elif warns or missing:
        verdict = "manual"
    else:
        verdict = "candidate"
    points = {PASS: 1.0, WARN: 0.5, FAIL: 0.0}
    scored = [(WEIGHTS[k], points[s]) for k, s in statuses.items() if s in points]
    total_w = sum(w for w, _ in scored)
    score = round(100 * sum(w * p for w, p in scored) / total_w) if total_w else None
    result.update(
        verdict=verdict,
        score=score,
        reasons={
            "fail": [CRITERIA_LABELS[k] for k in fails],
            "check": [CRITERIA_LABELS[k] for k in warns + missing],
        },
    )
    result["charts"] = _chart_payload(weekly, wk_bbw, wk_hi, wk_lo, daily, d_bbw, d_hi, d_lo, rs, base, vbp, pf)
    return result


def split_result(result: dict) -> tuple[dict, dict]:
    """Compact summary for lists/boards, and the heavy detail shown in the drawer."""
    phase = dict(result.get("phase") or {})
    history = phase.pop("history", [])
    summary = {k: result.get(k) for k in (
        "symbol", "name", "instrument_type", "last_date", "last_close", "verdict", "score",
        "error", "reasons", "pivot", "dist_pivot", "stockcharts_url")}
    summary["phase"] = phase or None
    summary["criteria"] = {k: {"status": c["status"], "value": c["value"]} for k, c in result.get("criteria", {}).items()}
    detail = {
        "criteria": result.get("criteria", {}),
        "charts": result.get("charts"),
        "pullbacks": result.get("pullbacks"),
        "phase_history": history,
    }
    return summary, detail


def _eps_criterion(symbol: str, th: Thresholds) -> dict:
    try:
        er = md.get_earnings(symbol)
    except Exception as e:  # earnings are optional data; never fail the ticker on them
        return _crit(NA, "n/a", f"EPS lookup failed: {e}")
    q = er.quarters
    if not q:
        return _crit(NA, "n/a", "No reported EPS found - check manually")
    last_date, last_eps = q[0]
    target = last_date - pd.Timedelta(days=365)
    prior = min(q[1:], key=lambda x: abs((x[0] - target).days), default=None)
    if prior is None or abs((prior[0] - target).days) > 45:
        status = PASS if last_eps > 0 else FAIL
        return _crit(
            WARN if status == PASS else FAIL,
            f"{last_eps:.2f}",
            f"Latest EPS {last_eps:.2f} ({last_date:%Y-%m-%d}); year-ago quarter not available ({er.source})",
            eps=_r(last_eps),
        )
    prior_eps = prior[1]
    if last_eps <= 0:
        status, growth_txt, growth = FAIL, "loss", None
    elif prior_eps <= 0:
        status, growth_txt, growth = PASS, "turnaround", None
    else:
        growth = (last_eps / prior_eps - 1) * 100
        growth_txt = f"{growth:+.0f}%"
        status = PASS if growth >= 0 else WARN if growth >= th.eps_warn_growth_pct else FAIL
    return _crit(
        status,
        growth_txt,
        f"EPS {last_eps:.2f} ({last_date:%Y-%m-%d}) vs {prior_eps:.2f} a year earlier ({prior[0]:%Y-%m-%d}); {er.source}",
        eps=_r(last_eps),
        prior_eps=_r(prior_eps),
        growth_pct=_r(growth, 1),
        ttm_eps=_r(sum(v for _, v in q[:4])) if len(q) >= 4 else None,
    )


def _chart_payload(weekly, wk_bbw, wk_hi, wk_lo, daily, d_bbw, d_hi, d_lo, rs, base, vbp, pf) -> dict:
    w = slice(-104, None)
    d = slice(-126, None)
    return {
        "weekly": {
            "dates": [x.strftime("%Y-%m-%d") for x in weekly.index[w]],
            "close": _series(weekly["Close"].iloc[w], 2),
            "ema10": _series(pf["ema10"].iloc[w], 2),
            "ema30": _series(pf["ema30"].iloc[w], 2),
            "bbw": _series(wk_bbw.iloc[w], 2),
            "chan_hi": _series(wk_hi.iloc[w], 2),
            "chan_lo": _series(wk_lo.iloc[w], 2),
            "base_start": base["start"].strftime("%Y-%m-%d") if base else None,
            "pivot": _r(base["pivot_high"], 2) if base else None,
        },
        "daily": {
            "dates": [x.strftime("%Y-%m-%d") for x in daily.index[d]],
            "bbw": _series(d_bbw.iloc[d], 2),
            "chan_hi": _series(d_hi.iloc[d], 2),
            "chan_lo": _series(d_lo.iloc[d], 2),
        },
        "rs": {
            "dates": [x.strftime("%Y-%m-%d") for x in pf.index[w]],
            "rs": _series(pf["rs"].iloc[w] * 1000, 3),
            "ema52": _series(pf["rs_ema"].iloc[w] * 1000, 3),
        },
        "vbp": volume_profile(vbp["Close"], vbp["Volume"]),
    }
