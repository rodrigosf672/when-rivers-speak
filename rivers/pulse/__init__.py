"""River Pulse: data for the animated 3D river map (the Studio ``pulse`` view).

The app notebook calls, in order:

    assets = ensure_assets(con)              # load, or download + derive on first run
    pack_water(assets), pack_relief_band(assets, band), pack_rivers(assets, label)
    discharge = discharge_matrix(con, assets)
    flow = flow_codes(discharge, baseline)  # baseline: "median" or "seasonal"
    pack_flow_year(discharge, flow, k)       # k = 0 .. YEAR_CELLS - 1, oldest year first
    meta, daily = summary(assets, discharge, flow)
    points = gauge_points(assets)

Heavy geo dependencies (pyproj, shapely, pyogrio, scipy, pillow) are imported
only when the layers are built.
"""
from __future__ import annotations

from .assets import PulseAssets, assets_ready, build_assets, ensure_assets, load_assets
from .flow import (
    Discharge,
    FlowCodes,
    discharge_matrix,
    flow_codes,
    gauge_points,
    pack_flow_year,
    summary,
)
from .packing import pack_relief_band, pack_rivers, pack_sections, pack_water, payload_kb
from .settings import BASELINES, RELIEF_BANDS, YEAR_CELLS

__all__ = [
    "BASELINES",
    "RELIEF_BANDS",
    "YEAR_CELLS",
    "Discharge",
    "FlowCodes",
    "PulseAssets",
    "assets_ready",
    "build_assets",
    "discharge_matrix",
    "ensure_assets",
    "flow_codes",
    "gauge_points",
    "load_assets",
    "pack_flow_year",
    "pack_relief_band",
    "pack_rivers",
    "pack_sections",
    "pack_water",
    "payload_kb",
    "summary",
]
