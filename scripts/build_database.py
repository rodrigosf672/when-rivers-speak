#!/usr/bin/env python
"""Build the DuckDB analytical database from partitioned Parquet.

Usage:
    python scripts/build_database.py
    RIVERS_DATA_DIR=data/full python scripts/build_database.py
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rivers import build_duckdb
from rivers.config import DATA_DIR


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=None,
                    help="base data dir containing parquet/ (default: config DATA_DIR)")
    args = ap.parse_args()
    base = Path(args.data_dir).resolve() if args.data_dir else DATA_DIR
    build_duckdb.build(parquet_dir=base / "parquet", duckdb_path=base / "rivers.duckdb")


if __name__ == "__main__":
    main()
