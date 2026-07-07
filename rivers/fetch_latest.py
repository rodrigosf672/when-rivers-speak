"""Fetch and normalize the latest river observations per state/parameter."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import normalize, usgs_api
from .config import MVP_PARAM_CODES, PARQUET_DIR, resolve_parameter


def fetch_state_param_latest(state: str, parameter: str, *, period: str = "P7D",
                             use_cache: bool = True) -> pd.DataFrame:
    """Fetch the most recent observation per site for one state+parameter."""
    p = resolve_parameter(parameter)
    js = usgs_api.get_latest_values_json(state, p.code, period=period,
                                         use_cache=use_cache)
    return normalize.parse_latest_json(js, state)


def fetch_latest(states: list[str], parameters: list[str] | None = None,
                 *, period: str = "P7D", base: Path | None = None,
                 use_cache: bool = True, write: bool = True,
                 log=print) -> pd.DataFrame:
    """Fetch latest observations across states/parameters.

    Designed to be cheap and re-runnable on a schedule (see
    ``scripts/update_latest.py``): each state/parameter slice is a small request
    and overwrites its own Parquet partition.
    """
    base = base or PARQUET_DIR
    parameters = parameters or list(MVP_PARAM_CODES)
    frames: list[pd.DataFrame] = []
    for st in states:
        for param in parameters:
            df = fetch_state_param_latest(st, param, period=period,
                                          use_cache=use_cache)
            n = len(df)
            if write and n:
                normalize.write_latest(df, base=base)
            if log:
                log(f"  latest {st} {param}: {n} sites")
            if n:
                frames.append(df)
    if not frames:
        return pd.DataFrame(columns=normalize.LATEST_COLUMNS)
    return pd.concat(frames, ignore_index=True)
