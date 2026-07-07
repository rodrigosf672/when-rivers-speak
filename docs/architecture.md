# Architecture

When Rivers Speak is a small, well-separated pipeline that turns public USGS
water data into a fast, interactive dashboard.

```text
┌───────────┐   ┌─────────────┐   ┌────────────────────┐   ┌──────────────────┐   ┌───────────┐
│ USGS API  │──►│  normalize  │──►│ partitioned Parquet │──►│ DuckDB summaries │──►│ marimo app│
│  (fetch)  │   │ (tidy data) │   │ state/param/year    │   │ (precomputed)    │   │ (queries) │
└───────────┘   └─────────────┘   └────────────────────┘   └──────────────────┘   └───────────┘
```

## Layers

### 1. Fetch (`rivers/usgs_api.py`, `rivers/fetch_*.py`)

A thin, retrying client wraps three USGS Water Services endpoints:

| Endpoint | Module call | Purpose |
|---|---|---|
| `/site`  | `get_sites_rdb`          | station metadata (RDB) |
| `/dv`    | `get_daily_values_json`  | historical daily statistics (WaterML/JSON) |
| `/iv`    | `get_latest_values_json` | recent instantaneous values (WaterML/JSON) |

The client adds exponential-backoff retries, a shared `User-Agent`, and an
on-disk response cache (`rivers/cache.py`). Fetchers iterate **one
(state, parameter, year) slice at a time** so no single request is huge and an
interrupted run resumes without refetching everything.

### 2. Normalize (`rivers/normalize.py`)

Raw payloads are parsed into tidy pandas frames with explicit schemas:

- `sites` — one row per gauge (id, name, coordinates, HUC, state).
- `daily` — long format, one row per site/parameter/day.
- `latest` — most recent instantaneous reading per site/parameter.

Frames are written as **Parquet partitioned by `state` / `parameter` / `year`**,
so incremental updates and reads touch only the slices that change.

### 3. Store (`rivers/build_duckdb.py`)

`build()` loads the Parquet partitions into a DuckDB database and precomputes
summary tables (see [`data_dictionary.md`](data_dictionary.md)). Anomaly scores
are computed in Python (`rivers/anomalies.py`) and registered into DuckDB,
because the composite score is easier to express and test as Python than as one
large SQL statement.

Tested query helpers (`latest_map_frame`, `site_series`,
`site_climatology_frame`, `site_list`) return **small, filtered** results — the
app never loads national data into pandas.

### 4. Analyze (`rivers/metrics.py`, `rivers/anomalies.py`)

Pure functions over per-site series: rolling means, percentile rank, day-of-year
climatology, robust z-score, seasonal deviation, rapid-change scores, flow
flags, completeness, and a volatility index. These feed the composite
**anomaly score** (defined in the data dictionary and the app's About tab).

### 5. Present (`app.py`, `rivers/charts.py`, `rivers/maps.py`)

A reactive [marimo](https://marimo.io) app with six tabs and a shared widget
bar. Charts are Altair; maps are deck.gl via pydeck (point, H3-hex, and
selected-site-highlight layers with a rich hover tooltip). Heavy/optional
dependencies (pydeck, h3) are imported lazily so the fetch+build path runs in a
minimal environment.

## Reactive dataflow

marimo builds a dependency graph across cells. The shared selection cell
(`_selection`) derives `sel_states`, `sel_param`, and `min_score` from the
widgets; each tab depends only on what it needs, so changing a filter re-runs
just the affected tabs. Every global name is unique across cells (a marimo
requirement, verified by compiling the app's `InternalApp` graph).

## Performance choices

- **Partitioned Parquet** keeps fetch/update/read costs proportional to what
  changed.
- **Precomputed summary tables** move aggregation out of the request path.
- **Small filtered queries** mean the app's memory footprint is independent of
  the dataset's total size.
- **Demo vs. full mode** lets the same code serve a tiny bundled sample or a
  large local store via `RIVERS_DATA_MODE` / `RIVERS_DATA_DIR`.

## Data modes

| Mode | `RIVERS_DATA_DIR` default | Use |
|---|---|---|
| `demo` | `data/sample` | bundled sample; instant startup; what Hugging Face serves |
| `full` | `data/full`   | larger locally-fetched store you build with `scripts/` |
