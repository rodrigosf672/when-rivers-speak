#!/usr/bin/env python
"""Fetch historical daily values partitioned by state / parameter / year.

Iterates one (state, parameter, year) slice at a time and writes each to its own
Parquet partition, so large national pulls stay memory-flat and resumable.

Usage:
    RIVERS_DATA_DIR=data/full python scripts/fetch_daily_partitioned.py \
        --states CA CO --params 00060 00065 --start 2015 --end 2024
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from rivers import fetch_daily
from rivers.config import DATA_DIR, MVP_PARAM_CODES, US_STATES


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--states", nargs="+", default=list(US_STATES.keys()))
    ap.add_argument("--params", nargs="+", default=list(MVP_PARAM_CODES))
    ap.add_argument("--start", type=int, default=date.today().year - 4)
    ap.add_argument("--end", type=int, default=date.today().year)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    parquet = (Path(args.out).resolve() if args.out else DATA_DIR) / "parquet"

    fetch_daily.fetch_daily(args.states, parameters=args.params,
                            start_year=args.start, end_year=args.end, base=parquet)
    print(f"[daily] done -> {parquet / 'daily'}")


if __name__ == "__main__":
    main()
