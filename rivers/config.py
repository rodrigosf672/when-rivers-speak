"""Central configuration for the When Rivers Speak observatory.

Holds the USGS parameter registry, data-mode / path resolution, and a handful
of small helpers shared across the package. Everything here is pure Python with
no heavy imports so it is cheap to import from the marimo app.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths and data modes
# --------------------------------------------------------------------------- #
# The package root is <repo>/rivers ; the repo root is its parent.
PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent

# Two runtime modes:
#   demo  -> load the small bundled sample dataset (fast, ships in the repo)
#   full  -> use a locally-built DuckDB/Parquet store (rebuild with scripts/)
DATA_MODE = os.environ.get("RIVERS_DATA_MODE", "demo").strip().lower()

# Where the Parquet partitions and DuckDB file live. In demo mode this points
# at the bundled sample; in full mode users typically point it at a larger
# locally-fetched store.
_default_dir = REPO_ROOT / ("data/sample" if DATA_MODE == "demo" else "data/full")
DATA_DIR = Path(os.environ.get("RIVERS_DATA_DIR", str(_default_dir))).resolve()

# Sub-paths inside DATA_DIR.
PARQUET_DIR = DATA_DIR / "parquet"        # partitioned normalized tables
DUCKDB_PATH = DATA_DIR / "rivers.duckdb"  # built analytical database

# Raw HTTP response cache (kept outside DATA_DIR so it is never shipped).
CACHE_DIR = Path(os.environ.get("RIVERS_CACHE_DIR", str(REPO_ROOT / ".cache"))).resolve()


def is_demo() -> bool:
    return DATA_MODE == "demo"


# --------------------------------------------------------------------------- #
# USGS parameter registry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Parameter:
    """A USGS NWIS parameter we know how to fetch and display."""
    code: str          # 5-digit USGS parameter code
    key: str           # short machine key used in file paths and the UI
    label: str         # human label for the UI
    unit: str          # display unit
    mvp: bool = False  # part of the minimum-viable dataset
    log_scale: bool = False  # sensible default axis scaling for charts


# Ordered registry. Discharge and gage height are the MVP parameters; the rest
# are supported by the fetchers and light up automatically once data exists.
PARAMETERS: tuple[Parameter, ...] = (
    Parameter("00060", "discharge", "Discharge / streamflow", "ft3/s", mvp=True, log_scale=True),
    Parameter("00065", "gage_height", "Gage height", "ft", mvp=True),
    Parameter("00010", "water_temp", "Water temperature", "degC"),
    Parameter("00400", "ph", "pH", "std units"),
    Parameter("00300", "dissolved_oxygen", "Dissolved oxygen", "mg/L"),
    Parameter("00095", "conductance", "Specific conductance", "uS/cm"),
    Parameter("63680", "turbidity", "Turbidity", "FNU"),
)

PARAM_BY_CODE = {p.code: p for p in PARAMETERS}
PARAM_BY_KEY = {p.key: p for p in PARAMETERS}
MVP_PARAM_CODES = tuple(p.code for p in PARAMETERS if p.mvp)


def resolve_parameter(code_or_key: str) -> Parameter:
    """Look a parameter up by either its USGS code or its short key."""
    s = str(code_or_key).strip()
    if s in PARAM_BY_CODE:
        return PARAM_BY_CODE[s]
    if s in PARAM_BY_KEY:
        return PARAM_BY_KEY[s]
    raise KeyError(f"Unknown parameter: {code_or_key!r}")


# --------------------------------------------------------------------------- #
# US states / territories used for national iteration
# --------------------------------------------------------------------------- #
# USGS uses 2-letter state postal codes for the stateCd query argument.
US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


def state_name(code: str) -> str:
    return US_STATES.get(str(code).upper(), str(code).upper())


# --------------------------------------------------------------------------- #
# Fetch / networking defaults
# --------------------------------------------------------------------------- #
USGS_BASE = "https://waterservices.usgs.gov/nwis"
HTTP_TIMEOUT = 60            # seconds
HTTP_MAX_RETRIES = 4
HTTP_BACKOFF = 1.5          # exponential backoff base (seconds)
# USGS asks that automated clients identify themselves.
USER_AGENT = "when-rivers-speak/0.1 (national river observatory; USGS public data)"

# Only surface-water stream sites with daily-value data by default.
DEFAULT_SITE_TYPE = "ST"    # stream
DEFAULT_DATA_TYPE = "dv"    # daily values
