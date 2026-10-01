"""Constants and paths for the River Pulse map layers.

The map plane is CONUS Albers (EPSG:5070) in metres. Alaska and Hawaii are
drawn as insets: projected in their own Albers, scaled, and shifted so the
lower-left corner of each inset frame lands at ``anchor``.
"""
from __future__ import annotations

from ..config import CACHE_DIR, DATA_DIR

# Raw public geography is downloaded on demand into the (gitignored) cache;
# the derived map layers live next to the rest of the data and are rebuilt with
# ``python scripts/build_pulse_assets.py``.
RAW_DIR = CACHE_DIR / "pulse" / "raw"
TILE_DIR = CACHE_DIR / "pulse" / "terrarium"
ASSET_DIR = DATA_DIR / "pulse"
ASSET_VERSION = 1

SOURCES = {
    "rivers_na": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_na_shp.zip",
    "rivers_ar": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_ar_shp.zip",
    "nation": "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_nation_20m.zip",
    "lakes": "https://naciscdn.org/naturalearth/10m/physical/ne_10m_lakes.zip",
}
TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"

REGIONS = {
    "conus": {"crs": "EPSG:5070", "scale": 1.0, "anchor": None, "zoom": 6,
              "lonlat": (-132.0, 19.0, -58.0, 53.5)},
    "ak": {"crs": "EPSG:3338", "scale": 0.36, "anchor": (-2400e3, -60e3), "zoom": 4,
           "lonlat": (-180.0, 50.5, -128.0, 72.0)},
    "hi": {"crs": "+proj=aea +lat_0=13 +lon_0=-157 +lat_1=8 +lat_2=18 +datum=NAD83 +units=m",
           "scale": 1.2, "anchor": (-1030e3, -30e3), "zoom": 7,
           "lonlat": (-160.6, 18.6, -154.5, 22.5)},
}
INSET_LABELS = {"ak": "Alaska", "hi": "Hawaii"}
MAP_EXTENT = (-2420e3, -80e3, 2300e3, 3210e3)  # xmin, ymin, xmax, ymax (m)
TERRAIN_WIDTH = 2048  # terrain grid width (px); about 2.3 km per pixel

MIN_UPLAND_KM2 = 300.0   # HydroRIVERS reaches drawn: upstream area at least this
SIMPLIFY_M = 600.0       # line simplification tolerance in map metres
SNAP_KM = 4.0            # max gauge-to-reach distance
SNAP_MAX_OCTAVES = 3.0   # max |log2(gauge mean / modelled reach mean)|
SPREAD_LIMIT_KM = 600.0  # how far a gauge's signal spreads along the network
MAJOR_UPLAND_KM2 = 2500.0  # reaches at least this big travel in the "major" payload

FLOW_LEVELS = 64         # colour codes 1..64 (0 = no data)
LOG2_RANGE = 3.0         # the colour scale spans 1/8x .. 8x of normal
CFS_TO_CMS = 0.028316846592
HOLD_GAP_DAYS = 10       # interior gaps up to this long are held from neighbouring days
# The map animates the last YEAR_CELLS calendar years of discharge, one app.py
# cell (river_pulse_year_0 .. _5, oldest first) per year.
YEAR_CELLS = 6

RELIEF_BANDS = 4         # relief rows travel in this many bands
# Studio sends each defining cell's values in one response of at most 1 MB.
PAYLOAD_LIMIT_KB = 950

BASELINES = {
    "Its own median over the record": "median",
    "Normal for the time of year": "seasonal",
}

SOURCE_LINES = [
    "Rivers: HydroRIVERS v1.0 (Lehner & Grill 2013); Hawaii traced from terrain",
    "Terrain: AWS Terrain Tiles (Mapzen, USGS, SRTM, ETOPO1)",
    "Outline: US Census cartographic boundary · Lakes: Natural Earth",
]
