"""Evaluation thresholds.

Every criterion has a "pass" bound and a "warn" bound. Values between the two
are borderline and send the ticker to "Check manually"; values beyond the warn
bound fail. All of them are shared team settings, edited in the UI (Settings) and
explained in its Guide, which is generated from THRESHOLD_META below.
"""

from dataclasses import dataclass, fields, asdict


@dataclass
class Thresholds:
    # Weekly BBW(20,2) position inside its CHAN(125); 0 = channel low, 1 = channel high.
    # Metric: the lowest position reached during the latest base.
    weekly_bbw_pass: float = 0.15
    weekly_bbw_warn: float = 0.30
    weekly_bbw_min_weeks: int = 8
    weekly_bbw_max_weeks: int = 26

    # Daily BBW(20,2): share of the last N sessions spent in the bottom half of the channel.
    daily_bbw_days: int = 30
    daily_bbw_pass: float = 0.60
    daily_bbw_warn: float = 0.40
    daily_bbw_ideal: float = 0.15  # "at or near the lows" bonus when min position <= this

    # RS line (close / $SPX): % below its 52-week high (best of the last 5 sessions).
    rs_pass_pct: float = 5.0
    rs_warn_pct: float = 10.0

    # Base length in weeks (since the base pivot high).
    base_pass_weeks: int = 14
    base_warn_weeks: int = 7
    base_max_depth_pct: float = 40.0
    base_breakout_grace_weeks: int = 2

    # Volatility contraction: avg daily range of the last N sessions / avg range of the
    # first third of the base.
    contraction_recent_days: int = 15
    contraction_pass: float = 0.80
    contraction_warn: float = 1.00
    contraction_bbw_now: float = 0.25  # also pass when daily BBW is currently this low in its channel
    pullback_zigzag_pct: float = 6.0  # minimum weekly swing; widened to 1.5x the median weekly range

    # Overhead supply: % of the last 2 years' volume that traded above the current price.
    overhead_pass_pct: float = 20.0
    overhead_warn_pct: float = 35.0
    overhead_lookback_days: int = 504

    # EPS: latest quarter vs the same quarter a year earlier.
    eps_warn_growth_pct: float = -5.0  # YoY between this and 0 is borderline

    # Phase classification (see screener/phases.py; defaults fitted to the team's dated phase calls)
    phase_exit_spread_pct: float = 1.5    # 2 -> 3 when 10W EMA is this % below 30W EMA ...
    phase_confirm_weeks: int = 2          # ... for this many weeks
    phase_reentry_margin_pct: float = 0.5  # 3 -> 2b when 10W is this % above 30W; also the "below" margin of a downtrend
    phase_early_2a_spread_pct: float = -1.0  # 1 -> 2a when 10W is within this % of 30W, rising, price above 30W
    phase_early_2a_slope_pct: float = 1.0  # ... with 10W rising at least this % over the slope window
    phase_mature_2a_weeks: int = 8        # 2a -> 2b after this many weeks of uptrend
    phase_down_weeks: int = 8             # weeks of rolling downtrend before 3/1 -> 4
    phase_four_exit_weeks: int = 3        # 4 -> 1 once the decline has stalled this many weeks
    phase_slope_weeks: int = 3            # EMA slopes are measured over this many weeks
    phase_rs_lead_pct: float = 8.0        # RS+ = RS line this % above its 52W EMA
    phase_rs_ema_weeks: int = 52
    phase_bbw_near_low: float = 0.15      # 2b+ = weekly BBW position (low of last 3 weeks) <= this
    phase_bbw_lookback_weeks: int = 3
    phase_bbw_half: float = 0.50          # 2b-AB if current weekly BBW position <= this, else 2b

    def phase_params(self):
        from .phases import PhaseParams
        return PhaseParams(
            ema_margin_pct=self.phase_reentry_margin_pct,
            exit_spread_pct=self.phase_exit_spread_pct,
            confirm_weeks=self.phase_confirm_weeks,
            early_2a_spread_pct=self.phase_early_2a_spread_pct,
            early_2a_slope_pct=self.phase_early_2a_slope_pct,
            mature_2a_weeks=self.phase_mature_2a_weeks,
            down_weeks=self.phase_down_weeks,
            four_exit_weeks=self.phase_four_exit_weeks,
            rs_lead_pct=self.phase_rs_lead_pct,
            bbw_near_low=self.phase_bbw_near_low,
            bbw_half=self.phase_bbw_half,
            bbw_lookback_weeks=self.phase_bbw_lookback_weeks,
            slope_weeks=self.phase_slope_weeks,
            rs_ema_weeks=self.phase_rs_ema_weeks,
        )

    @classmethod
    def from_overrides(cls, overrides: dict | None) -> "Thresholds":
        t = cls()
        if not overrides:
            return t
        for f in fields(cls):
            if f.name in overrides and overrides[f.name] is not None:
                try:
                    setattr(t, f.name, f.type(overrides[f.name]) if f.type in (int, float) else overrides[f.name])
                except (TypeError, ValueError):
                    pass
        return t

    def to_dict(self) -> dict:
        return asdict(self)


# Units stored as a 0-1 fraction but shown to people as a percentage.
FRACTION_UNITS = ("% of channel", "% of sessions")

# The UI's threshold editor and Guide: (section, group, intro, [(key, label, unit, help)]).
THRESHOLD_META = [
    ("phase", "Phase: trend (10W vs 30W EMA)",
     "The base phase (1 / 2a / 2b / 3 / 4) comes from a week-by-week state machine on weekly closes, "
     "so a phase depends on what came before it.", [
        ("phase_exit_spread_pct", "2 → 3: 10W below 30W EMA by at least", "%",
         "A phase 2 name becomes 3 once the 10W EMA is this far below the 30W EMA for the confirmation weeks. "
         "A young 2a (younger than '2a → 2b after') that fails goes back to 1 instead. Higher = names stay in 2 longer."),
        ("phase_confirm_weeks", "… for at least", "weeks",
         "Consecutive weeks the 2 → 3 condition must hold, so a one-week dip does not end phase 2."),
        ("phase_reentry_margin_pct", "3 → 2b: 10W back above 30W by at least", "%",
         "Phase 3 returns to 2b once the 10W EMA is this far above the 30W EMA and rising. The same margin "
         "(10W this far below the 30W) is part of the 'rolling downtrend' test for phase 4."),
        ("phase_early_2a_spread_pct", "1 → 2a: 10W vs 30W EMA above", "%",
         "Early 2a: a phase 1 name turns 2a once the 10W EMA is above this spread vs the 30W EMA "
         "(−1 = up to 1 % below it), the 10W is rising (next setting) and the price closes above the 30W EMA."),
        ("phase_early_2a_slope_pct", "… with 10W rising at least", "%",
         "Rise of the 10W EMA over the slope window that early 2a needs."),
        ("phase_mature_2a_weeks", "2a → 2b after", "weeks",
         "An uptrend that has lasted this many weeks is treated as 2b; no phase 3 is needed in between."),
        ("phase_down_weeks", "Weeks of rolling downtrend before 4", "weeks",
         "Rolling downtrend = 10W EMA below the 30W by the margin above, both EMAs falling, price below the 30W. "
         "3 → 4 when the 10W has been below the 30W this many weeks and the downtrend holds now; "
         "1 → 4 when the downtrend holds this many weeks in a row."),
        ("phase_four_exit_weeks", "4 → 1 once the decline stalls for", "weeks",
         "Consecutive weeks without the rolling-downtrend condition. Phase 4 always passes through 1 before 2a."),
        ("phase_slope_weeks", "Slope window", "weeks",
         "'Rising' and 'falling' EMAs are measured as the % change over this many weeks."),
    ]),
    ("phase", "Phase: 2a / 2b refinement (RS and squeeze)",
     "Phase 2 names are split by relative strength and by where the weekly BB Width(20,2) sits in its "
     "125-week price channel (0 % = at the channel low, 100 % = at the high).", [
        ("phase_bbw_near_low", "2b+: weekly BBW position at most", "% of channel",
         "2b becomes 2b+ when the lowest weekly BBW position of the last few weeks (next setting) is at or below this."),
        ("phase_bbw_lookback_weeks", "… lowest of the last", "weeks",
         "How many recent weeks count for the 2b+ squeeze."),
        ("phase_bbw_half", "2b-AB if current position at most", "% of channel",
         "A 2b that is not 2b+ is 2b-AB when the current weekly BBW position is at or below this, otherwise plain 2b."),
        ("phase_rs_lead_pct", "RS+ if RS line above its EMA by", "%",
         "Splits 2b+ into 2b+RS+ and 2b+RS-. RS line = close / S&P 500. "
         "2a+ uses a softer test: RS line above its EMA and that EMA rising over 4 weeks."),
        ("phase_rs_ema_weeks", "RS EMA length", "weeks",
         "The RS line is compared with its own EMA of this many weeks (52 = one year)."),
    ]),
    ("setup", "Base length",
     "Base = weeks since the base pivot high: the highest high of the longest recent window (up to 2 years) "
     "whose correction stays within the max depth.", [
        ("base_pass_weeks", "Pass if base at least", "weeks", "Base length that passes."),
        ("base_warn_weeks", "Borderline if at least", "weeks",
         "Shorter bases fail. No base at all (price trending, or the correction is deeper than the max depth) also fails."),
        ("base_max_depth_pct", "Max base depth", "%",
         "A correction deeper than this (pivot high to the lowest low since) is not a base; the search stops there."),
        ("base_breakout_grace_weeks", "Breakout grace", "weeks",
         "The last N weeks are ignored when looking for the pivot, so a fresh breakout still shows the base it came from."),
    ]),
    ("setup", "Weekly BBW",
     "Lowest position of the weekly BB Width(20,2) in its CHAN(125) during the base. The look-back is the base "
     "length, clamped to the min/max weeks below.", [
        ("weekly_bbw_pass", "Pass if lowest position at most", "% of channel", "At or below this passes."),
        ("weekly_bbw_warn", "Borderline if at most", "% of channel", "Above this fails."),
        ("weekly_bbw_min_weeks", "Look-back at least", "weeks", "Used when the base is shorter than this."),
        ("weekly_bbw_max_weeks", "Look-back at most", "weeks", "Used when the base is longer than this."),
    ]),
    ("setup", "Daily BBW",
     "Share of recent sessions with the daily BB Width(20,2) in the bottom half of its CHAN(125).", [
        ("daily_bbw_days", "Sessions checked", "sessions", "How many recent trading sessions are counted."),
        ("daily_bbw_pass", "Pass if share in bottom half at least", "% of sessions", "At or above this passes."),
        ("daily_bbw_warn", "Borderline if at least", "% of sessions", "Below this fails."),
        ("daily_bbw_ideal", "★ if lowest position at most", "% of channel",
         "Adds ★ (at/near the channel lows) to the value. Informational only, it does not change the grade."),
    ]),
    ("setup", "RS vs S&P 500",
     "RS line = daily close / S&P 500. Distance of the best RS value of the last 5 sessions from the RS line's "
     "52-week high.", [
        ("rs_pass_pct", "Pass if within … of the 52-week high", "%", "0 % = RS line at a new 52-week high."),
        ("rs_warn_pct", "Borderline if within", "%", "Further below the high fails."),
    ]),
    ("setup", "Volatility contraction",
     "Average daily range (high − low) / close of the last sessions, divided by the average range of the first "
     "third of the base. Below 1× = the stock has tightened up.", [
        ("contraction_recent_days", "Recent sessions compared", "sessions", "Length of the 'recent' window."),
        ("contraction_pass", "Pass if recent / early range at most", "×", "At or below this passes."),
        ("contraction_warn", "Borderline if at most", "×", "Above this fails."),
        ("contraction_bbw_now", "…or pass if daily BBW position now at most", "% of channel",
         "A daily BBW sitting this close to its 6-month low counts as contraction even if the range ratio does not pass."),
        ("pullback_zigzag_pct", "Min. pullback swing", "%",
         "Pullbacks inside the base are counted on weekly swings at least this big (or 1.5× the stock's median weekly "
         "range if larger). If the latest pullback is the deepest one and deeper than 8 %, a pass becomes borderline."),
    ]),
    ("setup", "Overhead supply",
     "Share of the look-back volume (volume by closing price, like StockCharts VbP) that traded above today's close.", [
        ("overhead_pass_pct", "Pass if volume above price at most", "%", "At or below this passes."),
        ("overhead_warn_pct", "Borderline if at most", "%", "Above this fails."),
        ("overhead_lookback_days", "Volume-by-price look-back", "sessions", "504 sessions ≈ 2 years."),
    ]),
    ("setup", "EPS",
     "Latest reported quarterly EPS vs the same quarter a year earlier (Yahoo adjusted EPS, or GAAP diluted EPS "
     "when Yahoo has too few quarters). ETFs and indexes are skipped.", [
        ("eps_warn_growth_pct", "Borderline if YoY growth at least", "%",
         "Growth ≥ 0 % passes, between this and 0 % is borderline, lower fails. A loss in the latest quarter fails; "
         "a loss a year ago that turned positive passes (turnaround). No year-ago quarter: borderline if EPS > 0."),
    ]),
]

# (smaller, larger): pairs that must stay ordered so the pass/borderline bands make sense.
ORDERED = [
    ("weekly_bbw_pass", "weekly_bbw_warn"),
    ("weekly_bbw_min_weeks", "weekly_bbw_max_weeks"),
    ("daily_bbw_warn", "daily_bbw_pass"),
    ("rs_pass_pct", "rs_warn_pct"),
    ("base_warn_weeks", "base_pass_weeks"),
    ("contraction_pass", "contraction_warn"),
    ("overhead_pass_pct", "overhead_warn_pct"),
    ("phase_bbw_near_low", "phase_bbw_half"),
]

# Settings that may be negative; every other value must be >= 0 (and >= 1 for weeks/days).
SIGNED = {"phase_early_2a_spread_pct", "eps_warn_growth_pct"}
ZERO_OK = {"base_breakout_grace_weeks"}


def threshold_labels() -> dict[str, tuple[str, str, str]]:
    """key -> (group, label, unit)."""
    return {k: (group, label, unit) for _, group, _, items in THRESHOLD_META for k, label, unit, _ in items}


def validate(t: Thresholds) -> list[str]:
    """Human-readable problems with a set of thresholds; empty when they are usable."""
    labels = threshold_labels()
    name = lambda k: f"{labels[k][0]} › {labels[k][1]}"
    errors = []
    for k, (_, _, unit) in labels.items():
        v = getattr(t, k)
        if unit in FRACTION_UNITS and not 0 <= v <= 1:
            errors.append(f"{name(k)} must be between 0 and 100 %")
        elif unit in ("weeks", "sessions") and v < (0 if k in ZERO_OK else 1):
            errors.append(f"{name(k)} must be at least {0 if k in ZERO_OK else 1}")
        elif k not in SIGNED and v < 0:
            errors.append(f"{name(k)} cannot be negative")
    if t.eps_warn_growth_pct > 0:
        errors.append(f"{name('eps_warn_growth_pct')} must be 0 % or lower (growth ≥ 0 % always passes)")
    for small, large in ORDERED:
        if getattr(t, small) > getattr(t, large):
            errors.append(f"{name(small)} ({getattr(t, small):g}) cannot be above {name(large)} ({getattr(t, large):g})")
    return errors
