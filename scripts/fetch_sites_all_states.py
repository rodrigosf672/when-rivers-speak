#!/usr/bin/env python
"""Fetch monitoring-site metadata for every US state (full-mode ingestion).

Writes one Parquet partition per state under ``<data>/parquet/sites``. Safe to
re-run: each state overwrites only its own partition.

Usage:
    RIVERS_DATA_DIR=data/full python scripts/fetch_sites_all_states.py
    python scripts/fetch_sites_all_states.py --states CA CO TX
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rivers import fetch_sites
from rivers.config import DATA_DIR, US_STATES


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--states", nargs="+", default=list(US_STATES.keys()))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    parquet = (Path(args.out).resolve() if args.out else DATA_DIR) / "parquet"

    total = 0
    for st in args.states:
        df = fetch_sites.fetch_mvp_sites([st], base=parquet)
        total += len(df)
        print(f"  {st}: {len(df)} sites")
    print(f"[sites] total {total} sites -> {parquet / 'sites'}")


if __name__ == "__main__":
    main()
