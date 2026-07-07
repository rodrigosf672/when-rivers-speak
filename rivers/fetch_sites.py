"""Fetch and normalize USGS monitoring-site metadata, state by state."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import normalize, usgs_api
from .config import MVP_PARAM_CODES, PARQUET_DIR


def fetch_state_sites(state: str, *, parameter_cd: str | None = None,
                      use_cache: bool = True) -> pd.DataFrame:
    """Return a tidy sites frame for one state.

    If ``parameter_cd`` is given, only sites reporting that parameter are
    returned; otherwise all stream sites with daily-value data are returned.
    """
    rdb = usgs_api.get_sites_rdb(state, parameter_cd=parameter_cd,
                                 use_cache=use_cache)
    return normalize.parse_sites_rdb(rdb, state)


def fetch_states_sites(states: list[str], *, parameter_cd: str | None = None,
                       base: Path | None = None, use_cache: bool = True,
                       write: bool = True) -> pd.DataFrame:
    """Fetch sites for several states, optionally writing per-state Parquet.

    Iterating per state keeps each request small and lets an interrupted run
    resume without refetching everything.
    """
    base = base or PARQUET_DIR
    frames: list[pd.DataFrame] = []
    for st in states:
        df = fetch_state_sites(st, parameter_cd=parameter_cd, use_cache=use_cache)
        if write and not df.empty:
            normalize.write_sites(df, base=base)
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=normalize.SITE_COLUMNS)
    return pd.concat(frames, ignore_index=True)


def fetch_mvp_sites(states: list[str], **kwargs) -> pd.DataFrame:
    """Convenience: sites that report at least one MVP parameter (discharge).

    Discharge is the most widely reported stream parameter, so filtering on it
    yields the core national gauge network.
    """
    return fetch_states_sites(states, parameter_cd=MVP_PARAM_CODES[0], **kwargs)
