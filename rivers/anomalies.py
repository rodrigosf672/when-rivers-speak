"""Composite anomaly scoring for river sites.

The anomaly score combines several independent signals so a site can look
unusual for different reasons — being at a record extreme, changing rapidly,
departing from its seasonal normal, or staying anomalous for a while — while
being penalized for thin data that would make any of those unreliable:

    anomaly_score =
        percentile_extremeness
      + rapid_change_component
      + seasonal_deviation_component
      + persistence_component
      - missing_data_penalty

Each component is scaled to roughly comparable magnitudes and the total is
clipped to ``[0, 100]`` for display. This is an *exploratory* situational-
awareness signal, not a calibrated hydrologic alert — see the About tab.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from . import metrics


@dataclass
class AnomalyResult:
    site_no: str
    parameter: str
    anomaly_score: float
    percentile: float
    zscore: float
    seasonal_deviation: float
    rise_score: float
    drop_score: float
    volatility: float
    completeness: float
    low_flow: bool
    high_flow: bool
    latest_value: float
    latest_date: str

    def as_dict(self) -> dict:
        return asdict(self)


def _percentile_extremeness(p: float) -> float:
    """Map a 0-100 percentile to 0-40 points, peaking at both tails.

    Distance from the median (50) drives the score, so record-high *and*
    record-low conditions both register as anomalous.
    """
    if np.isnan(p):
        return 0.0
    return float(abs(p - 50.0) / 50.0 * 40.0)


def _rapid_change_component(rise: float, drop: float) -> float:
    """Up to ~25 points for a large fractional day-over-day move."""
    m = np.nanmax([rise, drop, 0.0])
    if np.isnan(m):
        return 0.0
    return float(min(m, 1.0) * 25.0)


def _seasonal_component(dev: float) -> float:
    """Up to ~25 points for departure from the day-of-year normal."""
    if np.isnan(dev):
        return 0.0
    return float(min(abs(dev) / 3.0, 1.0) * 25.0)


def _persistence_component(series: pd.DataFrame, days: int = 7) -> float:
    """Up to 10 points if recent days sit persistently in a distribution tail.

    Rewards sustained unusual conditions over a single-day blip.
    """
    vals = pd.to_numeric(series["value"], errors="coerce").dropna()
    if len(vals) < days + 10:
        return 0.0
    lo, hi = vals.quantile(0.1), vals.quantile(0.9)
    recent = vals.iloc[-days:]
    frac_extreme = ((recent <= lo) | (recent >= hi)).mean()
    return float(frac_extreme * 10.0)


def _missing_penalty(completeness: float) -> float:
    """Subtract up to 20 points as data completeness drops toward zero."""
    if np.isnan(completeness):
        return 20.0
    return float((1.0 - completeness) * 20.0)


def score_site(series: pd.DataFrame, site_no: str, parameter: str) -> AnomalyResult:
    """Compute the composite anomaly score and its components for one series."""
    s = series.copy()
    s["date"] = pd.to_datetime(s["date"])
    s = s.sort_values("date")

    pctl = metrics.site_percentile(s)
    z = metrics.zscore_latest(s)
    dev = metrics.seasonal_deviation(s)
    rise, drop = metrics.rapid_change(s)
    vol = metrics.volatility_index(s)
    comp = metrics.completeness_score(s)
    low, high = metrics.flow_flags(s)

    score = (
        _percentile_extremeness(pctl)
        + _rapid_change_component(rise, drop)
        + _seasonal_component(dev)
        + _persistence_component(s)
        - _missing_penalty(comp)
    )
    score = float(np.clip(score, 0.0, 100.0))

    last = s.iloc[-1] if not s.empty else None
    return AnomalyResult(
        site_no=site_no, parameter=parameter, anomaly_score=round(score, 2),
        percentile=round(pctl, 2) if not np.isnan(pctl) else float("nan"),
        zscore=round(z, 3) if not np.isnan(z) else float("nan"),
        seasonal_deviation=round(dev, 3) if not np.isnan(dev) else float("nan"),
        rise_score=round(rise, 3) if not np.isnan(rise) else float("nan"),
        drop_score=round(drop, 3) if not np.isnan(drop) else float("nan"),
        volatility=round(vol, 3) if not np.isnan(vol) else float("nan"),
        completeness=round(comp, 3),
        low_flow=bool(low), high_flow=bool(high),
        latest_value=float(last["value"]) if last is not None else float("nan"),
        latest_date=str(last["date"].date()) if last is not None else "",
    )


def score_many(daily: pd.DataFrame) -> pd.DataFrame:
    """Score every (site_no, parameter) group in a long daily frame.

    Returns one row per site+parameter with the anomaly score and components,
    sorted by descending anomaly score.
    """
    if daily.empty:
        return pd.DataFrame(columns=[f.name for f in AnomalyResult.__dataclass_fields__.values()])
    out = []
    for (site_no, parameter), grp in daily.groupby(["site_no", "parameter"]):
        if grp["value"].notna().sum() < 10:
            continue
        out.append(score_site(grp, site_no, parameter).as_dict())
    df = pd.DataFrame(out)
    if not df.empty:
        df = df.sort_values("anomaly_score", ascending=False).reset_index(drop=True)
    return df


def anomaly_level(score: float) -> str:
    """Bucket a score into a coarse label for filters and color scales."""
    if np.isnan(score):
        return "unknown"
    if score >= 70:
        return "extreme"
    if score >= 50:
        return "high"
    if score >= 30:
        return "moderate"
    return "normal"
