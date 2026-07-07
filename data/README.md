# Data directory

This project ships a **small bundled sample** so the app runs immediately in
demo mode, and rebuilds a **larger local dataset** on demand for full mode.

## Layout

```text
data/
  sample/                 # bundled demo dataset (committed to the repo)
    parquet/
      sites/    state=XX/sites.parquet
      daily/    state=XX/parameter=<key>/year=YYYY/daily.parquet
      latest/   state=XX/parameter=<key>/latest.parquet
    rivers.duckdb         # built locally / in Docker (NOT committed)
  full/                   # full-mode data you fetch locally (gitignored)
```

Partitioning by `state` / `parameter` / `year` means fetches, incremental
updates, and DuckDB reads only touch the slices that change.

## Bundled sample scope

| Field       | Value |
|-------------|-------|
| States      | Rhode Island, Massachusetts, Colorado, California |
| Parameters  | Discharge / streamflow (`00060`), gage height (`00065`) |
| Daily range | 2021–present (daily mean, USGS statistic `00003`) |
| Latest      | Most recent instantaneous reading per site (trailing 7-day window) |
| Source      | USGS Water Services (`waterservices.usgs.gov`) |

The sample is a few megabytes of Parquet. The `rivers.duckdb` file is **built
from it** (`python scripts/build_database.py`) and is intentionally not
committed — it regenerates in seconds and would otherwise bloat the repo.

## Rebuilding

```bash
# Rebuild the bundled sample from USGS (few minutes):
python scripts/fetch_demo_data.py --states RI MA CO CA --params 00060 00065 --years 5
python scripts/build_database.py

# Build a larger local dataset (full mode):
export RIVERS_DATA_DIR=data/full
python scripts/fetch_sites_all_states.py
python scripts/fetch_daily_partitioned.py --states CA CO TX --start 2015 --end 2024
python scripts/update_latest.py --states CA CO TX
python scripts/build_database.py
```

## Data disclaimer

All data originates from the U.S. Geological Survey and is provisional until
reviewed by USGS. This project is an exploratory situational-awareness tool, not
a flood-prediction or emergency system. See the repository README and the app's
**About the Data** tab for the full limitations statement.
