#!/usr/bin/env python
"""Build the River Pulse map layers (terrain, river network, gauge slots).

Downloads the public geography once (about 110 MB: HydroRIVERS, AWS Terrain
Tiles, the Census US outline and Natural Earth lakes) into the cache and
derives the layers the ``pulse`` view needs from it and the DuckDB database.
The app notebook does the same on first run; use this to prebuild or refresh.

Usage:
    python scripts/build_pulse_assets.py
    python scripts/build_pulse_assets.py --force      # rebuild even if present
"""
from __future__ import annotations

import argparse

from rivers import build_duckdb, config, pulse
from rivers.pulse import settings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="rebuild even if the layers exist")
    args = ap.parse_args()
    if pulse.assets_ready() and not args.force:
        print(f"River Pulse layers already present in {settings.ASSET_DIR} (use --force to rebuild)")
        return
    if not config.DUCKDB_PATH.exists():
        if not config.PARQUET_DIR.exists():
            raise SystemExit(f"No database or Parquet under {config.DATA_DIR}. "
                             "Run scripts/fetch_demo_data.py then scripts/build_database.py.")
        build_duckdb.build()  # as the app does on first run
    con = build_duckdb.connect(read_only=True)
    try:
        pulse.build_assets(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
