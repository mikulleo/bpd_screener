"""Evaluation thresholds.

Every criterion has a "pass" bound and a "warn" bound. Values between the two
are borderline and send the ticker to "Check manually"; values beyond the warn
bound fail. The UI can override any of these per request.
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
    phase_early_2a_spread_pct: float = -1.0  # 1 -> 2a when 10W is within this % of 30W, rising, price above 30W
    phase_mature_2a_weeks: int = 8        # 2a -> 2b after this many weeks of uptrend
    phase_down_weeks: int = 8             # weeks of rolling downtrend before 3/1 -> 4
    phase_rs_lead_pct: float = 8.0        # RS+ = RS line this % above its 52W EMA
    phase_bbw_near_low: float = 0.15      # 2b+ = weekly BBW position (low of last 3 weeks) <= this
    phase_bbw_half: float = 0.50          # 2b-AB if current weekly BBW position <= this, else 2b

    def phase_params(self):
        from .phases import PhaseParams
        return PhaseParams(
            exit_spread_pct=self.phase_exit_spread_pct,
            confirm_weeks=self.phase_confirm_weeks,
            early_2a_spread_pct=self.phase_early_2a_spread_pct,
            mature_2a_weeks=self.phase_mature_2a_weeks,
            down_weeks=self.phase_down_weeks,
            rs_lead_pct=self.phase_rs_lead_pct,
            bbw_near_low=self.phase_bbw_near_low,
            bbw_half=self.phase_bbw_half,
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


# Labels shown in the UI's threshold editor, grouped by criterion.
THRESHOLD_META = [
    ("Phase classification", [
        ("phase_exit_spread_pct", "2 → 3: 10W below 30W EMA by %", "%"),
        ("phase_confirm_weeks", "… for weeks", "weeks"),
        ("phase_early_2a_spread_pct", "1 → 2a: 10W within % of 30W", "%"),
        ("phase_mature_2a_weeks", "2a → 2b after weeks", "weeks"),
        ("phase_down_weeks", "Weeks of downtrend before 4", "weeks"),
        ("phase_rs_lead_pct", "RS+ if RS above its 52W EMA by %", "%"),
        ("phase_bbw_near_low", "2b+ if weekly BBW position ≤", "ratio"),
        ("phase_bbw_half", "2b-AB if ≤ (else 2b)", "ratio"),
    ]),
    ("Weekly BBW", [
        ("weekly_bbw_pass", "Pass if lowest channel position ≤", "ratio"),
        ("weekly_bbw_warn", "Borderline if ≤", "ratio"),
        ("weekly_bbw_min_weeks", "Look-back: min weeks", "weeks"),
        ("weekly_bbw_max_weeks", "Look-back: max weeks", "weeks"),
    ]),
    ("Daily BBW", [
        ("daily_bbw_days", "Sessions checked", "days"),
        ("daily_bbw_pass", "Pass if share in bottom half ≥", "ratio"),
        ("daily_bbw_warn", "Borderline if ≥", "ratio"),
        ("daily_bbw_ideal", "Ideal: min position ≤", "ratio"),
    ]),
    ("Relative strength vs $SPX", [
        ("rs_pass_pct", "Pass if within % of 52-wk high", "%"),
        ("rs_warn_pct", "Borderline if within %", "%"),
    ]),
    ("Base", [
        ("base_pass_weeks", "Pass if base ≥ weeks", "weeks"),
        ("base_warn_weeks", "Borderline if ≥ weeks", "weeks"),
        ("base_max_depth_pct", "Max base depth %", "%"),
        ("base_breakout_grace_weeks", "Breakout grace weeks", "weeks"),
    ]),
    ("Volatility contraction", [
        ("contraction_recent_days", "Recent sessions compared", "days"),
        ("contraction_pass", "Pass if recent/early range ≤", "ratio"),
        ("contraction_warn", "Borderline if ≤", "ratio"),
        ("contraction_bbw_now", "Or pass if daily BBW position now ≤", "ratio"),
        ("pullback_zigzag_pct", "Min. weekly swing for pullbacks %", "%"),
    ]),
    ("Overhead supply", [
        ("overhead_pass_pct", "Pass if volume above price ≤ %", "%"),
        ("overhead_warn_pct", "Borderline if ≤ %", "%"),
        ("overhead_lookback_days", "Volume-by-price look-back", "days"),
    ]),
    ("EPS", [
        ("eps_warn_growth_pct", "Borderline if YoY growth ≥ %", "%"),
    ]),
]
