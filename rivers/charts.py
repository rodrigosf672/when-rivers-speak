"""Altair chart builders for the dashboard.

Each function takes tidy pandas frames (already filtered small by DuckDB) and
returns an ``alt.Chart``. Charts are deliberately self-contained and themed
consistently so they compose cleanly inside marimo cells.
"""
from __future__ import annotations

import altair as alt
import pandas as pd

# A calm, water-themed palette used across the app.
ANOMALY_SCALE = alt.Scale(
    domain=["normal", "moderate", "high", "extreme"],
    range=["#2c7fb8", "#7fcdbb", "#fdae61", "#d7191c"],
)


def _base(title: str = "") -> alt.Chart:
    return alt.Chart().properties(title=title, width="container", height=320)


def time_series(daily: pd.DataFrame, *, value_label: str = "value",
                title: str = "", anomalies: pd.DataFrame | None = None,
                climatology: pd.DataFrame | None = None) -> alt.Chart:
    """Historical time series with optional rolling means, seasonal band, and
    anomaly markers.

    Parameters
    ----------
    daily
        Frame with ``date``, ``value`` and optional ``roll7`` / ``roll30``.
    anomalies
        Optional frame with ``date``, ``value`` to overlay as points.
    climatology
        Optional frame with ``date``, ``p25``, ``p75`` for a shaded normal band.
    """
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    layers: list[alt.Chart] = []

    if climatology is not None and not climatology.empty:
        c = climatology.copy()
        c["date"] = pd.to_datetime(c["date"])
        layers.append(
            alt.Chart(c).mark_area(opacity=0.18, color="#2c7fb8").encode(
                x=alt.X("date:T", title="Date"),
                y=alt.Y("p25:Q", title=value_label),
                y2="p75:Q",
            )
        )

    layers.append(
        alt.Chart(d).mark_line(color="#253494", strokeWidth=1).encode(
            x=alt.X("date:T", title="Date"),
            y=alt.Y("value:Q", title=value_label),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("value:Q", format=",.1f")],
        )
    )
    for col, color in (("roll7", "#41b6c4"), ("roll30", "#fd8d3c")):
        if col in d.columns:
            layers.append(
                alt.Chart(d).mark_line(color=color, strokeWidth=2).encode(
                    x="date:T", y=f"{col}:Q",
                )
            )
    if anomalies is not None and not anomalies.empty:
        a = anomalies.copy()
        a["date"] = pd.to_datetime(a["date"])
        layers.append(
            alt.Chart(a).mark_point(color="#d7191c", size=60, filled=True).encode(
                x="date:T", y="value:Q",
                tooltip=[alt.Tooltip("date:T"), alt.Tooltip("value:Q", format=",.1f")],
            )
        )
    return alt.layer(*layers).properties(
        title=title, width="container", height=340
    ).interactive()


def state_ranking(state_summary: pd.DataFrame, *, metric: str = "anomaly_burden",
                  title: str = "State anomaly burden") -> alt.Chart:
    """Horizontal bar chart ranking states by an anomaly metric."""
    d = state_summary.copy().sort_values(metric, ascending=False).head(30)
    return alt.Chart(d).mark_bar(color="#2c7fb8").encode(
        x=alt.X(f"{metric}:Q", title=metric.replace("_", " ").title()),
        y=alt.Y("state:N", sort="-x", title="State"),
        tooltip=["state:N", alt.Tooltip(f"{metric}:Q", format=",.1f")],
    ).properties(title=title, width="container", height=500)


def score_distribution(scores: pd.DataFrame, *, by: str = "state",
                       title: str = "Anomaly score distribution") -> alt.Chart:
    """Boxplot of anomaly scores grouped by a categorical column."""
    d = scores.dropna(subset=["anomaly_score"]).copy()
    return alt.Chart(d).mark_boxplot(color="#41b6c4").encode(
        x=alt.X(f"{by}:N", title=by.title()),
        y=alt.Y("anomaly_score:Q", title="Anomaly score"),
    ).properties(title=title, width="container", height=340)


def coverage_bars(coverage: pd.DataFrame, *, title: str = "Data coverage") -> alt.Chart:
    """Grouped bars of site counts by state and parameter."""
    d = coverage.copy()
    return alt.Chart(d).mark_bar().encode(
        x=alt.X("state:N", title="State"),
        y=alt.Y("n_sites:Q", title="Monitoring sites"),
        color=alt.Color("parameter:N", title="Parameter"),
        tooltip=["state:N", "parameter:N", "n_sites:Q", "total_obs:Q"],
    ).properties(title=title, width="container", height=340)


def change_ranking(scores: pd.DataFrame, *, direction: str = "rise",
                   title: str | None = None) -> alt.Chart:
    """Top sites by sudden rise or drop score."""
    col = "rise_score" if direction == "rise" else "drop_score"
    d = scores.dropna(subset=[col]).sort_values(col, ascending=False).head(20).copy()
    d["label"] = d["site_no"] + " (" + d["state"].fillna("?") + ")"
    return alt.Chart(d).mark_bar(
        color="#d7191c" if direction == "rise" else "#2b83ba"
    ).encode(
        x=alt.X(f"{col}:Q", title=f"{direction.title()} score"),
        y=alt.Y("label:N", sort="-x", title="Site"),
        tooltip=["site_no:N", "state:N", alt.Tooltip(f"{col}:Q", format=".2f")],
    ).properties(title=title or f"Top sudden {direction}s", width="container", height=420)
