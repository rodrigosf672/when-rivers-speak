# Data dictionary

All tables live in the DuckDB database built by `scripts/build_database.py`.
Base tables mirror the Parquet partitions; summary tables are precomputed for
speed.

## Parameters

| Code | Key | Label | Unit | MVP |
|---|---|---|---|---|
| 00060 | `discharge` | Discharge / streamflow | ft³/s | ✓ |
| 00065 | `gage_height` | Gage height | ft | ✓ |
| 00010 | `water_temp` | Water temperature | °C | |
| 00400 | `ph` | pH | std units | |
| 00300 | `dissolved_oxygen` | Dissolved oxygen | mg/L | |
| 00095 | `conductance` | Specific conductance | µS/cm | |
| 63680 | `turbidity` | Turbidity | FNU | |

## Base tables

### `sites`
| Column | Type | Notes |
|---|---|---|
| site_no | VARCHAR | USGS site number (primary key) |
| station_nm | VARCHAR | station name |
| site_tp_cd | VARCHAR | site type (e.g. `ST` stream) |
| latitude, longitude | DOUBLE | decimal degrees (NAD83) |
| coord_acy_cd | VARCHAR | coordinate accuracy code |
| datum | VARCHAR | coordinate datum |
| altitude | DOUBLE | gage/land-surface altitude |
| huc_cd | VARCHAR | hydrologic unit code |
| state | VARCHAR | 2-letter state code |

### `daily_values`
One row per site/parameter/day. `date_d` is the `date` cast to DATE.

| Column | Type |
|---|---|
| site_no, parameter, param_code, state | VARCHAR |
| date, date_d | VARCHAR / DATE |
| value | DOUBLE |
| qualifiers | VARCHAR (e.g. `A`=approved, `P`=provisional) |
| year | BIGINT |

### `latest_values`
Most recent instantaneous reading per site/parameter.

| Column | Type |
|---|---|
| site_no, parameter, param_code, state | VARCHAR |
| datetime | VARCHAR (ISO 8601 with offset) |
| value | DOUBLE |
| qualifiers | VARCHAR |

## Summary tables

| Table | Grain | Key columns |
|---|---|---|
| `site_daily_summary` | site × parameter | n_days, first/last_date, mean/median/min/max/std, p10/p90 |
| `site_monthly_summary` | site × parameter × year × month | n_days, mean/min/max |
| `state_daily_summary` | state × parameter × date | n_sites, mean/median |
| `state_monthly_summary` | state × parameter × year × month | n_sites, n_obs, mean |
| `site_climatology` | site × parameter × day-of-year | median, p25, p75, n |
| `site_anomaly_scores` | site × parameter | anomaly_score + components (below) |
| `state_anomaly_summary` | state × parameter | n_sites, mean/max_anomaly, n_elevated, anomaly_burden |
| `data_coverage_summary` | state × parameter | n_sites, total_obs, mean_days_per_site, mean_completeness, earliest/latest_record |

## `site_anomaly_scores` columns

| Column | Meaning |
|---|---|
| anomaly_score | composite 0–100 (see below) |
| anomaly_level | `normal` / `moderate` / `high` / `extreme` |
| percentile | empirical percentile of the latest value in the site record |
| zscore | robust z-score (median/MAD) of the latest value |
| seasonal_deviation | latest value vs day-of-year normal, in robust units |
| rise_score, drop_score | fractional day-over-day change vs 30-day mean |
| volatility | 30-day coefficient of variation |
| completeness | fraction of expected daily obs present |
| low_flow, high_flow | latest value ≤10th / ≥90th site percentile |
| latest_value, latest_date | most recent daily value and its date |

## Anomaly score

Clipped to `[0, 100]`:

```text
anomaly_score =
    percentile_extremeness          # 0–40, distance of percentile from median
  + rapid_change_component          # 0–25, max(|rise|,|drop|) fractional move
  + seasonal_deviation_component    # 0–25, departure from day-of-year normal
  + persistence_component           # 0–10, fraction of recent days in a tail
  − missing_data_penalty            # 0–20, grows as completeness drops
```

It flags conditions that are **unusual relative to a gauge's own record**.
It is a heuristic for exploration — **not** a calibrated hydrologic forecast.

## Qualifier codes (common)

| Code | Meaning |
|---|---|
| `A` | Approved for publication |
| `P` | Provisional, subject to revision |
| `e` | Estimated |

See the USGS documentation for the full list.
