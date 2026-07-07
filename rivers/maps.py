"""High-performance map builders using pydeck.

The dashboard's national views can render thousands of gauge points, so we use
deck.gl (via ``pydeck``) rather than a per-marker map. Two layer styles are
offered:

* **Site points** — one dot per gauge, colored by anomaly level and sized by
  anomaly magnitude, with a rich hover tooltip.
* **H3 hexes** — points aggregated into H3 cells for an uncluttered national
  overview, colored by the mean anomaly score in each cell.

pydeck and h3 are imported lazily so the rest of the package (fetchers, DuckDB
build, metrics) imports and runs even in a minimal environment without the
mapping stack. Call :func:`mapping_available` to check at runtime.
"""
from __future__ import annotations

import pandas as pd

# Anomaly level -> RGBA color (matches charts.ANOMALY_SCALE).
LEVEL_COLORS = {
    "normal":   [44, 127, 184, 160],
    "moderate": [127, 205, 187, 180],
    "high":     [253, 174, 97, 210],
    "extreme":  [215, 25, 28, 230],
    "unknown":  [150, 150, 150, 120],
}

# Continental-US default view.
DEFAULT_VIEW = dict(latitude=39.5, longitude=-98.35, zoom=3.4, pitch=0, bearing=0)

TOOLTIP_HTML = (
    "<b>{station_nm}</b><br/>"
    "Site {site_no} &middot; {state}<br/>"
    "{parameter}: <b>{latest_value}</b><br/>"
    "Percentile: {percentile} &middot; Anomaly: {anomaly_score}<br/>"
    "Completeness: {completeness}<br/>"
    "Updated: {latest_date}"
)


def mapping_available() -> bool:
    """True if pydeck is importable in the current environment."""
    try:
        import pydeck  # noqa: F401
        return True
    except Exception:
        return False


def h3_available() -> bool:
    try:
        import h3  # noqa: F401
        return True
    except Exception:
        return False


def _color_for(level: str) -> list[int]:
    return LEVEL_COLORS.get(str(level), LEVEL_COLORS["unknown"])


def _prep_points(df: pd.DataFrame) -> pd.DataFrame:
    """Attach color and radius columns used by the point layer."""
    d = df.copy()
    d = d.dropna(subset=["latitude", "longitude"])
    if "anomaly_level" not in d.columns:
        d["anomaly_level"] = "unknown"
    d["color"] = d["anomaly_level"].map(_color_for)
    score = pd.to_numeric(d.get("anomaly_score"), errors="coerce").fillna(0.0)
    # Radius in metres: 6 km floor, scaling with anomaly magnitude.
    d["radius"] = 6000 + score * 900
    # String-format fields for the tooltip (avoids NaN showing as 'nan').
    for c in ("station_nm", "state", "parameter", "latest_date"):
        if c in d.columns:
            d[c] = d[c].fillna("").astype(str)
    for c, fmt in (("latest_value", "{:,.1f}"), ("percentile", "{:.0f}"),
                   ("anomaly_score", "{:.0f}"), ("completeness", "{:.0%}")):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce").map(
                lambda v, f=fmt: (f.format(v) if pd.notna(v) else "n/a"))
    return d


def site_point_layer(df: pd.DataFrame):
    """Return a pydeck ScatterplotLayer of gauge sites (or None if unavailable)."""
    if not mapping_available():
        return None
    import pydeck as pdk
    d = _prep_points(df)
    return pdk.Layer(
        "ScatterplotLayer",
        data=d,
        get_position="[longitude, latitude]",
        get_fill_color="color",
        get_radius="radius",
        radius_min_pixels=2,
        radius_max_pixels=40,
        pickable=True,
        opacity=0.85,
        stroked=True,
        get_line_color=[255, 255, 255, 120],
        line_width_min_pixels=0.5,
    )


def highlight_layer(df: pd.DataFrame, site_no: str):
    """A ring layer highlighting a single selected site."""
    if not mapping_available():
        return None
    import pydeck as pdk
    sel = df[df["site_no"] == site_no].dropna(subset=["latitude", "longitude"])
    if sel.empty:
        return None
    return pdk.Layer(
        "ScatterplotLayer", data=sel,
        get_position="[longitude, latitude]",
        get_fill_color=[0, 0, 0, 0],
        get_line_color=[20, 20, 20, 255],
        get_radius=20000, radius_min_pixels=8, line_width_min_pixels=3,
        stroked=True, filled=False, pickable=False,
    )


def h3_layer(df: pd.DataFrame, resolution: int = 4):
    """Aggregate sites into H3 hexes colored by mean anomaly score."""
    if not (mapping_available() and h3_available()):
        return None
    import h3
    import pydeck as pdk

    d = df.dropna(subset=["latitude", "longitude"]).copy()
    if d.empty:
        return None
    # h3 v4 uses latlng_to_cell; v3 uses geo_to_h3.
    to_cell = getattr(h3, "latlng_to_cell", None) or getattr(h3, "geo_to_h3")
    d["h3"] = [to_cell(lat, lon, resolution)
               for lat, lon in zip(d["latitude"], d["longitude"])]
    agg = d.groupby("h3").agg(
        mean_anomaly=("anomaly_score", "mean"),
        n_sites=("site_no", "count"),
    ).reset_index()
    # Map mean anomaly (0-100) to a blue->red ramp.
    def ramp(v):
        v = 0 if pd.isna(v) else max(0, min(100, v)) / 100
        return [int(44 + v * 171), int(127 - v * 100), int(184 - v * 156), 190]
    agg["color"] = agg["mean_anomaly"].map(ramp)
    return pdk.Layer(
        "H3HexagonLayer", data=agg,
        get_hexagon="h3", get_fill_color="color",
        pickable=True, extruded=False, opacity=0.55,
    )


def build_deck(df: pd.DataFrame, *, layer: str = "points",
               selected_site: str | None = None, h3_resolution: int = 4,
               map_style: str | None = None):
    """Assemble a pydeck Deck for the given layer style.

    ``layer`` is one of ``"points"`` or ``"h3"``. Returns None when the mapping
    stack is unavailable so callers can fall back to a table/chart.
    """
    if not mapping_available():
        return None
    import pydeck as pdk

    layers = []
    if layer == "h3":
        hl = h3_layer(df, resolution=h3_resolution)
        if hl is not None:
            layers.append(hl)
    if layer == "points" or not layers:
        pl = site_point_layer(df)
        if pl is not None:
            layers.append(pl)
    if selected_site:
        hs = highlight_layer(df, selected_site)
        if hs is not None:
            layers.append(hs)

    return pdk.Deck(
        layers=layers,
        initial_view_state=pdk.ViewState(**DEFAULT_VIEW),
        map_style=map_style or "light",
        tooltip={"html": TOOLTIP_HTML,
                 "style": {"backgroundColor": "#0b3d5c", "color": "white",
                           "fontSize": "12px", "padding": "8px"}},
    )
