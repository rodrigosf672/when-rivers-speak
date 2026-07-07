"""Hydrologic metrics computed over per-site daily time series.

All functions are pure and operate on pandas objects so they can be unit-tested
without a database and reused both in the DuckDB build (to precompute summary
tables) and in the marimo app (for on-the-fly series the user selects).

Conventions
-----------
* A *site series* is a frame with at least ``date`` (datetime-like) and
  ``value`` columns, sorted or sortable by date, for a single site+parameter.
* Rolling windows are calendar-day based via a daily reindex, so gaps do not
  silently shorten a 7- or 30-day window.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _as_daily(series: pd.DataFrame) -> pd.DataFrame:
    """Return a copy indexed by a continuous daily DatetimeIndex."""
    s = series.copy()
    s["date"] = pd.to_datetime(s["date"])
    s = s.sort_values("date").drop_duplicates("date").set_index("date")
    if s.empty:
        return s
    full = pd.date_range(s.index.min(), s.index.max(), freq="D")
    return s.reindex(full)


# --------------------------------------------------------------------------- #
# Rolling statistics
# --------------------------------------------------------------------------- #
def rolling_means(series: pd.DataFrame, windows=(7, 30)) -> pd.DataFrame:
    """Add ``roll{w}`` rolling-mean columns (min_periods = w // 2)."""
    d = _as_daily(series)
    for w in windows:
        d[f"roll{w}"] = d["value"].rolling(w, min_periods=max(1, w // 2)).mean()
    return d.reset_index(names="date")


def volatility_index(series: pd.DataFrame, window: int = 30) -> float:
    """Coefficient of variation of daily values over the trailing window.

    A unitless measure of how much a river is bouncing around; higher means
    flashier flow. Returns ``nan`` if there is too little data.
    """
    d = _as_daily(series)["value"].dropna()
    if len(d) < 5:
        return float("nan")
    tail = d.iloc[-window:]
    m = tail.mean()
    if not m or np.isnan(m):
        return float("nan")
    return float(tail.std(ddof=0) / abs(m))


# --------------------------------------------------------------------------- #
# Percentiles and climatology
# --------------------------------------------------------------------------- #
def site_percentile(series: pd.DataFrame, value: float | None = None) -> float:
    """Percentile rank (0-100) of ``value`` within the site's full record.

    Defaults to the most recent value. This is the empirical CDF position, so
    100 means "highest ever seen at this gauge", 0 means "lowest ever".
    """
    vals = pd.to_numeric(series["value"], errors="coerce").dropna()
    if vals.empty:
        return float("nan")
    if value is None:
        value = vals.iloc[-1]
    return float((vals < value).mean() * 100.0)


def day_of_year_climatology(series: pd.DataFrame) -> pd.DataFrame:
    """Median and IQR of value by day-of-year across all years on record.

    Returns a frame indexed 1..366 with ``median``, ``p25``, ``p75`` columns —
    the seasonal "normal" envelope used for seasonal-deviation scoring and the
    percentile bands in the historical chart.
    """
    s = series.copy()
    s["date"] = pd.to_datetime(s["date"])
    s["doy"] = s["date"].dt.dayofyear
    g = s.groupby("doy")["value"]
    clim = pd.DataFrame({
        "median": g.median(),
        "p25": g.quantile(0.25),
        "p75": g.quantile(0.75),
        "n": g.size(),
    })
    return clim.reindex(range(1, 367))


def zscore_latest(series: pd.DataFrame) -> float:
    """Robust z-score of the latest value vs the site's overall distribution.

    Uses median / MAD (scaled) so a few extreme spikes do not swallow the
    scale. Returns ``nan`` with insufficient data.
    """
    vals = pd.to_numeric(series["value"], errors="coerce").dropna()
    if len(vals) < 10:
        return float("nan")
    med = vals.median()
    mad = (vals - med).abs().median()
    if mad == 0:
        return 0.0
    return float((vals.iloc[-1] - med) / (1.4826 * mad))


def seasonal_deviation(series: pd.DataFrame) -> float:
    """How far the latest value sits above/below its day-of-year normal.

    Expressed in robust units relative to the seasonal IQR, so +2 means the
    river is well above what is typical for this time of year.
    """
    s = series.copy()
    s["date"] = pd.to_datetime(s["date"])
    if s.empty:
        return float("nan")
    clim = day_of_year_climatology(s)
    last = s.sort_values("date").iloc[-1]
    doy = int(pd.Timestamp(last["date"]).dayofyear)
    row = clim.loc[doy] if doy in clim.index else None
    if row is None or pd.isna(row["median"]):
        return float("nan")
    iqr = (row["p75"] - row["p25"])
    # Fall back through IQR -> |median| -> overall spread -> 1.0, and floor the
    # scale so a near-zero seasonal spread (e.g. an intermittent/dry creek) does
    # not blow the deviation up to an absurd magnitude.
    overall = pd.to_numeric(s["value"], errors="coerce")
    spread = float(overall.std(ddof=0)) if overall.notna().sum() > 2 else 0.0
    scale = 0.0
    for cand in (iqr, abs(row["median"]), spread):
        if cand and not np.isnan(cand) and cand > 0:
            scale = cand
            break
    if scale <= 0:
        scale = 1.0
    # Absolute floor of 1% of the record's typical magnitude.
    floor = 0.01 * (abs(float(overall.median())) if overall.notna().any() else 1.0)
    scale = max(scale, floor, 1e-6)
    return float((last["value"] - row["median"]) / scale)


# --------------------------------------------------------------------------- #
# Rapid change
# --------------------------------------------------------------------------- #
def rapid_change(series: pd.DataFrame, window: int = 1) -> tuple[float, float]:
    """Return (rise_score, drop_score) for the latest ``window``-day change.

    Scores are the fractional change relative to the trailing 30-day mean,
    clipped at 0 for the direction that does not apply. A value of 0.5 means the
    river jumped by ~50% of its recent typical level.
    """
    d = _as_daily(series)["value"]
    if d.dropna().shape[0] < window + 2:
        return float("nan"), float("nan")
    recent = d.iloc[-1]
    prior = d.iloc[-(window + 1)]
    baseline = d.iloc[-31:-1].mean()
    if not baseline or np.isnan(baseline):
        return float("nan"), float("nan")
    delta = (recent - prior) / abs(baseline)
    return float(max(delta, 0.0)), float(max(-delta, 0.0))


# --------------------------------------------------------------------------- #
# Flags and completeness
# --------------------------------------------------------------------------- #
def flow_flags(series: pd.DataFrame, low_pct: float = 10.0,
               high_pct: float = 90.0) -> tuple[bool, bool]:
    """(low_flow, high_flow) flags for the latest value vs site percentiles."""
    p = site_percentile(series)
    if np.isnan(p):
        return False, False
    return p <= low_pct, p >= high_pct


def completeness_score(series: pd.DataFrame) -> float:
    """Fraction (0-1) of expected daily observations actually present."""
    d = _as_daily(series)
    if d.empty:
        return 0.0
    return float(d["value"].notna().mean())
