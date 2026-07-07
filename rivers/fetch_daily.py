"""Fetch and normalize historical daily values, chunked by year and parameter.

National daily-value pulls are large, so this module deliberately fetches one
(state, parameter, year) slice at a time. Each slice is normalized and written
to its own Parquet partition, which keeps memory flat and makes runs resumable
and incrementally updatable.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from . import normalize, usgs_api
from .config import MVP_PARAM_CODES, PARQUET_DIR, resolve_parameter


def _year_ranges(start_year: int, end_year: int):
    for y in range(start_year, end_year + 1):
        yield y, f"{y}-01-01", f"{y}-12-31"


def fetch_state_param_year(state: str, parameter: str, year: int,
                           *, use_cache: bool = True) -> pd.DataFrame:
    """Fetch one (state, parameter, year) slice of daily means."""
    p = resolve_parameter(parameter)
    _, start, end = next(iter(_year_ranges(year, year)))
    # Never request a future window past today.
    today = date.today().isoformat()
    if end > today:
        end = today
    js = usgs_api.get_daily_values_json(state, p.code, start, end,
                                        use_cache=use_cache)
    return normalize.parse_daily_json(js, state)


def fetch_daily(states: list[str], parameters: list[str] | None = None,
                start_year: int | None = None, end_year: int | None = None,
                *, base: Path | None = None, use_cache: bool = True,
                write: bool = True, log=print) -> pd.DataFrame:
    """Fetch daily values across states/parameters/years, one slice at a time.

    Returns the concatenation of all slices (handy for small demo pulls). For
    large national runs, rely on the per-slice Parquet writes and ignore the
    return value.
    """
    base = base or PARQUET_DIR
    parameters = parameters or list(MVP_PARAM_CODES)
    end_year = end_year or date.today().year
    start_year = start_year or (end_year - 1)

    frames: list[pd.DataFrame] = []
    for st in states:
        for param in parameters:
            for year, _, _ in _year_ranges(start_year, end_year):
                df = fetch_state_param_year(st, param, year, use_cache=use_cache)
                n = len(df)
                if write and n:
                    normalize.write_daily(df, base=base)
                if log:
                    log(f"  daily {st} {param} {year}: {n} rows")
                if n:
                    frames.append(df)
    if not frames:
        return pd.DataFrame(columns=normalize.DAILY_COLUMNS)
    return pd.concat(frames, ignore_index=True)
