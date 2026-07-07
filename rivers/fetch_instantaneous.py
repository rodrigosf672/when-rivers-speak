"""Fetch and store a trailing window of 15-minute instantaneous river data.

USGS Instantaneous Values (IV) serves sub-hourly readings. We keep a short
trailing window (default 30 days) so the app can show real-time-monitoring
views — each parameter's recent high-frequency signal against its normal range —
without storing the multi-gigabyte full history that six years of 15-minute data
nationwide would require.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import normalize, usgs_api
from .config import MVP_PARAM_CODES, PARQUET_DIR, resolve_parameter


def fetch_state_param_iv(state: str, parameter: str, *, period: str = "P30D",
                         use_cache: bool = True) -> pd.DataFrame:
    """Fetch the full 15-minute series for one state+parameter over ``period``."""
    p = resolve_parameter(parameter)
    js = usgs_api.get_latest_values_json(state, p.code, period=period,
                                         use_cache=use_cache)
    return normalize.parse_instantaneous_json(js, state)


def fetch_instantaneous(states: list[str], parameters: list[str] | None = None,
                        *, period: str = "P30D", base: Path | None = None,
                        use_cache: bool = True, write: bool = True,
                        log=print) -> pd.DataFrame:
    """Fetch a trailing 15-minute window across states/parameters.

    Each state/parameter slice is one IV request and overwrites its own Parquet
    partition, so a scheduled refresh replaces the window atomically.
    """
    base = base or PARQUET_DIR
    parameters = parameters or list(MVP_PARAM_CODES)
    frames: list[pd.DataFrame] = []
    for st in states:
        for param in parameters:
            df = fetch_state_param_iv(st, param, period=period, use_cache=use_cache)
            n = len(df)
            if write and n:
                normalize.write_instantaneous(df, base=base)
            if log:
                log(f"  iv {st} {param}: {n} readings ({df['site_no'].nunique() if n else 0} sites)")
            if n:
                frames.append(df)
    if not frames:
        return pd.DataFrame(columns=normalize.INSTANTANEOUS_COLUMNS)
    return pd.concat(frames, ignore_index=True)
