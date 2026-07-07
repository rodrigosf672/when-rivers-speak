"""When Rivers Speak — a national river observatory built on public USGS data.

The package is organized as a small pipeline:

    usgs_api  -> raw HTTP client (cached, retrying)
    normalize -> raw payloads to tidy pandas / partitioned Parquet
    fetch_*   -> per-state/parameter/year fetch orchestration
    build_duckdb -> analytical DuckDB database + precomputed summary tables
    metrics / anomalies -> hydrologic metrics and composite anomaly scoring
    charts / maps -> Altair charts and pydeck maps for the marimo app

``config`` holds the parameter registry, data-mode resolution, and shared
constants. Heavy/optional dependencies (pydeck, h3, altair) are imported lazily
inside their modules so the core fetch + build path runs in a minimal env.

This is an exploratory, situational-awareness tool for making river change
visible. It is not a flood-prediction system and carries no emergency
reliability guarantees.
"""
from __future__ import annotations

from . import config  # noqa: F401

__version__ = "0.1.0"
__all__ = [
    "config",
    "usgs_api",
    "normalize",
    "fetch_sites",
    "fetch_daily",
    "fetch_latest",
    "build_duckdb",
    "metrics",
    "anomalies",
    "charts",
    "maps",
    "cache",
]
