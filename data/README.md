# Data directory

This project ships a **small bundled sample** so the app runs immediately in
demo mode, and rebuilds a **larger local dataset** on demand for full mode.

## Layout

```text
data/
  sample/                 # bundled demo dataset (committed to the repo)
    parquet/
      sites/          state=XX/sites.parquet
      daily/          state=XX/parameter=<key>/year=YYYY/daily.parquet
      latest/         state=XX/parameter=<key>/latest.parquet
      instantaneous/  state=XX/parameter=<key>/iv.parquet   # trailing ~30-day 15-min window
    rivers.duckdb         # built locally / in Docker (NOT committed)
  full/                   # full-mode data you fetch locally (gitignored)
```

Partitioning by `state` / `parameter` / `year` means fetches, incremental
updates, and DuckDB reads only touch the slices that change.

## Bundled dataset scope

| Field       | Value |
|-------------|-------|
| States      | All 50 states + DC (national coverage) |
| Sites       | ~26,000 monitoring sites |
| Parameters  | Discharge (`00060`), gage height (`00065`), water temperature (`00010`), specific conductance (`00095`), dissolved oxygen (`00300`), pH (`00400`) |
| Daily range | 2021–present (daily mean, USGS statistic `00003`); ~30.4M observations |
| Latest      | Most recent instantaneous reading per site (trailing 7-day window) |
| 15-minute   | Trailing ~30-day window of instantaneous readings, all 6 parameters (~80M readings, ~150 MB Parquet) |
| Source      | USGS Water Services (`waterservices.usgs.gov`) |

The bundled dataset is ~230 MB of partitioned Parquet (~80 MB daily + ~150 MB
15-minute). The `rivers.duckdb` file is **built from it**
(`python scripts/build_database.py`) and is intentionally not committed — it
regenerates from the Parquet in a couple of minutes. The 15-minute layer is
**not materialized** into the DB (it would balloon to ~2 GB); it is exposed as a
lazy view over its Parquet at connect time, so the built DB stays ~440 MB.

## Rebuilding

```bash
# Rebuild the national bundled dataset from USGS (~30 min, all 51 states):
python scripts/fetch_demo_data.py \
  --states AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO \
           MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC \
  --params 00060 00065 --years 6
python scripts/build_database.py

# Or a quick subset for local iteration:
python scripts/fetch_demo_data.py --states RI MA CO CA --params 00060 00065 --years 2
python scripts/build_database.py

# Build a deeper-history local dataset (full mode):
export RIVERS_DATA_DIR=data/full
python scripts/fetch_sites_all_states.py
python scripts/fetch_daily_partitioned.py --states CA CO TX --start 2000 --end 2024
python scripts/update_latest.py --states CA CO TX
python scripts/build_database.py
```

## Data disclaimer

All data originates from the U.S. Geological Survey and is provisional until
reviewed by USGS. This project is an exploratory situational-awareness tool, not
a flood-prediction or emergency system. See the repository README and the app's
**About the Data** tab for the full limitations statement.
