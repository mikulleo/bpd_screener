import numpy as np
import pandas as pd

from screener.phases import PhaseParams, base_phase_history, classify, refine, weekly_features


def _weekly(closes):
    idx = pd.date_range("2021-01-01", periods=len(closes), freq="W-FRI")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1.0}), pd.Series(100.0, index=idx)


def _phases(closes, **kw):
    w, bench = _weekly(closes)
    p = PhaseParams(**kw)
    return base_phase_history(weekly_features(w, bench, p), p)


def test_full_cycle_runs_through_the_stages():
    # decline (4) -> long flat base (1) -> rally (2a -> 2b after maturing)
    closes = list(np.linspace(200, 100, 60)) + [100 + np.sin(i / 2) for i in range(40)] + list(np.linspace(100, 180, 40))
    hist = _phases(closes)
    seen = [p for i, p in enumerate(hist) if p and (i == 0 or p != hist.iloc[i - 1])]
    assert seen[:1] == ["4"]
    assert "1" in seen and "2a" in seen and seen[-1] == "2b"
    assert seen.index("1") < seen.index("2a") < seen.index("2b")


def test_pause_in_uptrend_is_phase_3_then_2b():
    # steady uptrend, a ~11% pullback that pulls 10W under 30W, then the uptrend resumes
    closes = list(np.linspace(100, 140, 80)) + list(np.linspace(140, 125, 8)) + [125] * 3 + list(np.linspace(125, 170, 20))
    hist = list(_phases(closes))
    i3 = hist.index("3")
    assert hist[i3 - 1] == "2b"
    assert "2b" in hist[i3:]
    assert "4" not in hist[i3:]


def test_short_breakdown_stays_3_long_decline_becomes_4():
    up = list(np.linspace(100, 140, 80))
    short = _phases(up + list(np.linspace(140, 125, 8)) + [125] * 4)
    assert short.iloc[-1] == "3"
    long = _phases(up + list(np.linspace(140, 70, 30)))
    assert long.iloc[-1] == "4"


def test_refine_splits_2b_by_squeeze_and_rs():
    p = PhaseParams()
    assert refine("2b", 12.0, True, 0.10, 0.08, p) == "2b+RS+"
    assert refine("2b", 3.0, True, 0.10, 0.08, p) == "2b+RS-"
    assert refine("2b", 12.0, True, 0.40, 0.30, p) == "2b-AB"
    assert refine("2b", 12.0, True, 0.70, 0.60, p) == "2b"
    assert refine("2a", 2.0, True, 0.5, 0.5, p) == "2a+"
    assert refine("2a", 2.0, False, 0.5, 0.5, p) == "2a"
    assert refine("3", 20.0, True, 0.1, 0.1, p) == "3"


def test_classify_reports_context():
    closes = list(np.linspace(100, 200, 120))
    w, bench = _weekly(closes)
    r = classify(w, bench)
    assert r["base_phase"] == "2b" and r["weeks_in_phase"] > 0
    assert len(r["history"]) == 52
    assert isinstance(r["flags"], list) and isinstance(r["doubts"], list)
