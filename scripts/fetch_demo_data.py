#!/usr/bin/env python
"""Fetch the small bundled demo dataset used by the app in demo mode.

Pulls sites, ~2 years of daily discharge + gage height, and latest observations
for a handful of states, then writes partitioned Parquet under ``data/sample``.
Kept intentionally small so it ships in the GitHub repo and rebuilds in minutes.

Usage:
    python scripts/fetch_demo_data.py
    python scripts/fetch_demo_data.py --states RI MA CO CA --years 2

Environment:
    RIVERS_DATA_DIR   override output directory (default: data/sample)
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from rivers import fetch_daily, fetch_latest, fetch_sites
from rivers.config import DATA_DIR, MVP_PARAM_CODES

DEMO_STATES = ["RI", "MA", "CO", "CA"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--states", nargs="+", default=DEMO_STATES)
    ap.add_argument("--params", nargs="+", default=list(MVP_PARAM_CODES),
                    help="USGS parameter codes (default: discharge + gage height)")
    ap.add_argument("--years", type=int, default=2, help="years of daily history")
    ap.add_argument("--out", default=None, help="output dir (default: data/sample)")
    args = ap.parse_args()

    base = Path(args.out).resolve() if args.out else DATA_DIR
    parquet = base / "parquet"
    end_year = date.today().year
    start_year = end_year - (args.years - 1)

    print(f"[demo] output -> {parquet}")
    print(f"[demo] states={args.states} params={args.params} "
          f"years={start_year}-{end_year}")

    print("[demo] fetching sites ...")
    sites = fetch_sites.fetch_mvp_sites(args.states, base=parquet)
    print(f"[demo] sites: {len(sites)} rows across {sites['state'].nunique()} states")

    print("[demo] fetching daily values ...")
    fetch_daily.fetch_daily(args.states, parameters=args.params,
                            start_year=start_year, end_year=end_year, base=parquet)

    print("[demo] fetching latest observations ...")
    fetch_latest.fetch_latest(args.states, parameters=args.params, base=parquet)

    print("[demo] done. Build the database with: python scripts/build_database.py")


if __name__ == "__main__":
    main()
