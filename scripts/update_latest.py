#!/usr/bin/env python
"""Refresh the latest observations and rebuild latest-dependent tables.

Designed for scheduled runs (see .github/workflows/update-data.yml): it fetches
recent instantaneous values for the configured states/parameters, rewrites the
``latest`` Parquet partitions, and rebuilds the DuckDB database so the app picks
up fresh conditions. This is a lightweight operation compared with the daily
history pull.

Usage:
    python scripts/update_latest.py
    python scripts/update_latest.py --states RI MA CO CA
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rivers import build_duckdb, fetch_latest
from rivers.config import DATA_DIR, MVP_PARAM_CODES

DEFAULT_STATES = ["RI", "MA", "CO", "CA"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--states", nargs="+", default=DEFAULT_STATES)
    ap.add_argument("--params", nargs="+", default=list(MVP_PARAM_CODES))
    ap.add_argument("--out", default=None)
    ap.add_argument("--rebuild", action="store_true", default=True,
                    help="rebuild DuckDB after updating (default: on)")
    args = ap.parse_args()

    base = Path(args.out).resolve() if args.out else DATA_DIR
    parquet = base / "parquet"

    print(f"[update] refreshing latest for {args.states} -> {parquet / 'latest'}")
    df = fetch_latest.fetch_latest(args.states, parameters=args.params,
                                   base=parquet, use_cache=False)
    print(f"[update] latest: {len(df)} rows")

    if args.rebuild:
        build_duckdb.build(parquet_dir=parquet, duckdb_path=base / "rivers.duckdb")
        print("[update] database rebuilt")


if __name__ == "__main__":
    main()
