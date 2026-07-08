"""Build the analytical DuckDB database from partitioned Parquet.

The database is the app's query engine: base tables mirror the Parquet
partitions, and a set of precomputed summary tables keep the dashboard fast so
the marimo app never has to load national data into pandas — it issues small,
filtered DuckDB queries and gets back only what a widget selection needs.

Tables built
------------
Base:      sites, daily_values, latest_values
Summaries: site_daily_summary, site_monthly_summary,
           state_daily_summary, state_monthly_summary,
           site_climatology, site_anomaly_scores,
           state_anomaly_summary, data_coverage_summary

The anomaly-related tables are computed in Python (via ``anomalies.py``) and
registered into DuckDB, since the composite score is easier to express and test
as pure Python than as one large SQL statement.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from . import anomalies
from .config import DUCKDB_PATH, PARQUET_DIR, state_name


def _glob(base: Path, table: str) -> str:
    """Return a DuckDB-friendly recursive glob for a partitioned table."""
    return str(base / table / "**" / "*.parquet")


def _has_parquet(base: Path, table: str) -> bool:
    return any((base / table).rglob("*.parquet")) if (base / table).exists() else False


def build(parquet_dir: Path | None = None, duckdb_path: Path | None = None,
          *, log=print) -> Path:
    """Build (or rebuild) the DuckDB database. Returns the DB path."""
    parquet_dir = parquet_dir or PARQUET_DIR
    duckdb_path = duckdb_path or DUCKDB_PATH
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    if duckdb_path.exists():
        duckdb_path.unlink()

    con = duckdb.connect(str(duckdb_path))
    try:
        _build_base_tables(con, parquet_dir, log=log)
        _build_summary_tables(con, log=log)
        _build_anomaly_tables(con, log=log)
        _build_coverage_table(con, log=log)
        con.execute("CHECKPOINT")
    finally:
        con.close()
    log(f"Built DuckDB at {duckdb_path}")
    return duckdb_path


# --------------------------------------------------------------------------- #
# Base tables
# --------------------------------------------------------------------------- #
def _build_base_tables(con, base: Path, *, log) -> None:
    if _has_parquet(base, "sites"):
        con.execute(f"CREATE TABLE sites AS SELECT * FROM read_parquet('{_glob(base, 'sites')}', union_by_name=true)")
    else:
        con.execute("CREATE TABLE sites (site_no VARCHAR, station_nm VARCHAR, site_tp_cd VARCHAR, latitude DOUBLE, longitude DOUBLE, coord_acy_cd VARCHAR, datum VARCHAR, altitude DOUBLE, huc_cd VARCHAR, state VARCHAR)")

    # daily_values (~30M rows) and instantaneous_values (~80M rows) are NOT
    # materialized into the DB — they would dominate its size (daily alone is
    # ~340 MB materialized vs ~75 MB as Parquet; IV would add ~2 GB). Both are
    # exposed as lazy views over their hive-partitioned Parquet, created by
    # ``_attach_lazy_views`` at build time (so the summary/climatology/anomaly
    # tables can be computed from them) and again at ``connect()`` time (so the
    # shipped DB carries no build-time path and the views resolve wherever the
    # repo is deployed). Only the small base tables (sites, latest_values) and
    # the precomputed summary tables are materialized — keeping the DB compact.
    if _has_parquet(base, "latest"):
        con.execute(f"CREATE TABLE latest_values AS SELECT * FROM read_parquet('{_glob(base, 'latest')}', union_by_name=true)")
    else:
        con.execute("CREATE TABLE latest_values (site_no VARCHAR, parameter VARCHAR, param_code VARCHAR, datetime VARCHAR, value DOUBLE, qualifiers VARCHAR, state VARCHAR)")

    _attach_lazy_views(con, base)

    for t in ("sites", "latest_values"):
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        log(f"  base {t}: {n} rows")
    if _has_parquet(base, "daily"):
        n = con.execute("SELECT count(*) FROM daily_values").fetchone()[0]
        log(f"  daily_values (lazy view): {n} rows")
    if _has_parquet(base, "instantaneous"):
        log("  instantaneous_values: lazy view over Parquet")


def _attach_lazy_views(con, base: Path) -> None:
    """(Re)create the lazy views over large partitioned Parquet tables.

    Called at build time and at ``connect()`` time. Uses TEMPORARY views so it
    works on a read-only connection and always targets the current data
    directory. Falls back to empty typed tables when the Parquet is absent.
    """
    if _has_parquet(base, "daily"):
        con.execute(
            "CREATE OR REPLACE TEMPORARY VIEW daily_values AS "
            "SELECT *, CAST(date AS DATE) AS date_d "
            f"FROM read_parquet('{_glob(base, 'daily')}', union_by_name=true, hive_partitioning=true)"
        )
    elif not _table_exists(con, "daily_values"):
        con.execute("CREATE TABLE daily_values (site_no VARCHAR, parameter VARCHAR, param_code VARCHAR, date VARCHAR, value DOUBLE, qualifiers VARCHAR, state VARCHAR, year BIGINT, date_d DATE)")

    if _has_parquet(base, "instantaneous"):
        con.execute(
            "CREATE OR REPLACE TEMPORARY VIEW instantaneous_values AS "
            "SELECT *, CAST(datetime AS TIMESTAMP) AS ts "
            f"FROM read_parquet('{_glob(base, 'instantaneous')}', union_by_name=true, hive_partitioning=true)"
        )
    elif not _table_exists(con, "instantaneous_values"):
        con.execute("CREATE TABLE instantaneous_values (site_no VARCHAR, parameter VARCHAR, param_code VARCHAR, datetime VARCHAR, value DOUBLE, qualifiers VARCHAR, state VARCHAR, date VARCHAR, ts TIMESTAMP)")


def _table_exists(con, name: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [name]).fetchone()[0] > 0


# --------------------------------------------------------------------------- #
# Summary tables (pure SQL over daily_values)
# --------------------------------------------------------------------------- #
def _build_summary_tables(con, *, log) -> None:
    con.execute("""
        CREATE TABLE site_daily_summary AS
        SELECT site_no, parameter, state,
               count(*)              AS n_days,
               min(date_d)           AS first_date,
               max(date_d)           AS last_date,
               avg(value)            AS mean_value,
               median(value)         AS median_value,
               min(value)            AS min_value,
               max(value)            AS max_value,
               stddev_pop(value)     AS std_value,
               quantile_cont(value, 0.1) AS p10_value,
               quantile_cont(value, 0.9) AS p90_value
        FROM daily_values
        GROUP BY site_no, parameter, state
    """)

    con.execute("""
        CREATE TABLE site_monthly_summary AS
        SELECT site_no, parameter, state,
               year, month(date_d) AS month,
               count(*)   AS n_days,
               avg(value) AS mean_value,
               min(value) AS min_value,
               max(value) AS max_value
        FROM daily_values
        GROUP BY site_no, parameter, state, year, month(date_d)
    """)

    con.execute("""
        CREATE TABLE state_daily_summary AS
        SELECT state, parameter, date_d AS date,
               count(*)   AS n_sites,
               avg(value) AS mean_value,
               median(value) AS median_value
        FROM daily_values
        GROUP BY state, parameter, date_d
    """)

    con.execute("""
        CREATE TABLE state_monthly_summary AS
        SELECT state, parameter, year, month(date_d) AS month,
               count(DISTINCT site_no) AS n_sites,
               count(*)   AS n_obs,
               avg(value) AS mean_value
        FROM daily_values
        GROUP BY state, parameter, year, month(date_d)
    """)

    # Day-of-year climatology per site/parameter.
    con.execute("""
        CREATE TABLE site_climatology AS
        SELECT site_no, parameter, state,
               dayofyear(date_d) AS doy,
               count(*)   AS n,
               median(value) AS median_value,
               quantile_cont(value, 0.25) AS p25_value,
               quantile_cont(value, 0.75) AS p75_value
        FROM daily_values
        GROUP BY site_no, parameter, state, dayofyear(date_d)
    """)

    for t in ("site_daily_summary", "site_monthly_summary", "state_daily_summary",
              "state_monthly_summary", "site_climatology"):
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        log(f"  summary {t}: {n} rows")


# --------------------------------------------------------------------------- #
# Anomaly tables (Python-computed, registered into DuckDB)
# --------------------------------------------------------------------------- #
def _build_anomaly_tables(con, *, log) -> None:
    daily = con.execute(
        "SELECT site_no, parameter, param_code, state, date_d AS date, value "
        "FROM daily_values"
    ).df()

    scores = anomalies.score_many(daily)
    if not scores.empty:
        # Attach state + coarse level + site metadata for mapping/filtering.
        meta = con.execute(
            "SELECT site_no, parameter, param_code, state FROM site_daily_summary "
            "s JOIN daily_values d USING (site_no, parameter, state) GROUP BY 1,2,3,4"
        ).df()
        # site_daily_summary lacks param_code; derive from daily instead.
        pc = daily[["site_no", "parameter", "param_code", "state"]].drop_duplicates()
        scores = scores.merge(pc, on=["site_no", "parameter"], how="left")
        scores["anomaly_level"] = scores["anomaly_score"].map(anomalies.anomaly_level)
    else:
        scores = pd.DataFrame(columns=[
            "site_no", "parameter", "anomaly_score", "percentile", "zscore",
            "seasonal_deviation", "rise_score", "drop_score", "volatility",
            "completeness", "low_flow", "high_flow", "latest_value",
            "latest_date", "param_code", "state", "anomaly_level"])

    con.register("_scores_df", scores)
    con.execute("CREATE TABLE site_anomaly_scores AS SELECT * FROM _scores_df")
    con.unregister("_scores_df")

    # Per-state anomaly burden: mean/max score and count of elevated sites.
    con.execute("""
        CREATE TABLE state_anomaly_summary AS
        SELECT state, parameter,
               count(*)                                   AS n_sites,
               avg(anomaly_score)                         AS mean_anomaly,
               max(anomaly_score)                         AS max_anomaly,
               sum(CASE WHEN anomaly_score >= 50 THEN 1 ELSE 0 END) AS n_elevated,
               avg(anomaly_score) *
                   sum(CASE WHEN anomaly_score >= 50 THEN 1 ELSE 0 END) AS anomaly_burden
        FROM site_anomaly_scores
        WHERE state IS NOT NULL
        GROUP BY state, parameter
    """)

    for t in ("site_anomaly_scores", "state_anomaly_summary"):
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        log(f"  anomaly {t}: {n} rows")


# --------------------------------------------------------------------------- #
# Coverage
# --------------------------------------------------------------------------- #
def _build_coverage_table(con, *, log) -> None:
    con.execute("""
        CREATE TABLE data_coverage_summary AS
        WITH per_site AS (
            SELECT s.state, s.parameter, s.site_no,
                   s.n_days, s.first_date, s.last_date,
                   date_diff('day', s.first_date, s.last_date) + 1 AS span_days
            FROM site_daily_summary s
        )
        SELECT state, parameter,
               count(DISTINCT site_no) AS n_sites,
               sum(n_days)             AS total_obs,
               avg(n_days)             AS mean_days_per_site,
               avg(CASE WHEN span_days > 0
                        THEN n_days::DOUBLE / span_days ELSE NULL END) AS mean_completeness,
               min(first_date)         AS earliest_record,
               max(last_date)          AS latest_record
        FROM per_site
        GROUP BY state, parameter
    """)
    n = con.execute("SELECT count(*) FROM data_coverage_summary").fetchone()[0]
    log(f"  coverage data_coverage_summary: {n} rows")


def connect(duckdb_path: Path | None = None, read_only: bool = True,
            parquet_dir: Path | None = None):
    """Open a connection to the built database (read-only by default).

    The large ``daily_values`` and ``instantaneous_values`` tables are exposed
    as temporary views over their hive-partitioned Parquet (see
    ``_build_base_tables`` for why they are not materialized). The views are
    session-scoped, so they work on a read-only connection and always point at
    the current ``parquet_dir`` — the shipped DB carries no build-time path.
    """
    duckdb_path = duckdb_path or DUCKDB_PATH
    parquet_dir = parquet_dir or PARQUET_DIR
    con = duckdb.connect(str(duckdb_path), read_only=read_only)
    _attach_lazy_views(con, parquet_dir)
    return con


# --------------------------------------------------------------------------- #
# Small, tested query helpers used by the app (keep filtered results small)
# --------------------------------------------------------------------------- #
def latest_map_frame(con, *, parameter: str, states: list[str] | None = None,
                     min_anomaly: float | None = None):
    """Latest observations joined to site metadata + anomaly scores.

    This is the frame the National River Pulse map consumes. Explicit table
    aliases avoid the ambiguous ``state`` column shared across the joined
    tables.
    """
    clauses = ["l.parameter = ?", "s.latitude IS NOT NULL"]
    params: list = [parameter]
    if states:
        placeholders = ",".join("?" for _ in states)
        clauses.append(f"l.state IN ({placeholders})")
        params.extend(states)
    if min_anomaly is not None:
        clauses.append("a.anomaly_score >= ?")
        params.append(min_anomaly)
    where = " AND ".join(clauses)
    sql = f"""
        SELECT l.site_no, l.state AS state, l.parameter,
               l.value AS latest_value, l.datetime AS latest_date,
               s.station_nm, s.latitude, s.longitude, s.huc_cd,
               a.anomaly_score, a.anomaly_level, a.percentile,
               a.rise_score, a.drop_score, a.volatility, a.completeness
        FROM latest_values l
        JOIN sites s ON l.site_no = s.site_no
        LEFT JOIN site_anomaly_scores a
               ON l.site_no = a.site_no AND l.parameter = a.parameter
              AND l.state = a.state
        WHERE {where}
    """
    return con.execute(sql, params).df()


def site_series(con, *, site_no: str, parameter: str, start=None, end=None):
    """Full (or date-bounded) daily series for one site+parameter."""
    clauses = ["site_no = ?", "parameter = ?"]
    params: list = [site_no, parameter]
    if start:
        clauses.append("date_d >= ?"); params.append(str(start))
    if end:
        clauses.append("date_d <= ?"); params.append(str(end))
    where = " AND ".join(clauses)
    return con.execute(
        f"SELECT date_d AS date, value FROM daily_values WHERE {where} ORDER BY date_d",
        params).df()


def site_climatology_frame(con, *, site_no: str, parameter: str):
    """Day-of-year climatology (median, p25, p75) for one site+parameter."""
    return con.execute(
        "SELECT doy, median_value AS median, p25_value AS p25, p75_value AS p75 "
        "FROM site_climatology WHERE site_no = ? AND parameter = ? ORDER BY doy",
        [site_no, parameter]).df()


def site_list(con, *, parameter: str, states: list[str] | None = None):
    """Sites (with names) reporting a parameter, optionally limited to states."""
    clauses = ["d.parameter = ?"]
    params: list = [parameter]
    if states:
        placeholders = ",".join("?" for _ in states)
        clauses.append(f"d.state IN ({placeholders})")
        params.extend(states)
    where = " AND ".join(clauses)
    return con.execute(
        f"""SELECT d.site_no, d.state, s.station_nm, d.n_days, d.first_date, d.last_date
            FROM site_daily_summary d LEFT JOIN sites s ON d.site_no = s.site_no
            WHERE {where} ORDER BY d.state, s.station_nm""", params).df()


def site_iv_series(con, *, site_no: str, parameter: str, start=None, end=None):
    """Trailing-window 15-minute instantaneous series for one site+parameter.

    Returns a datetime-ordered frame (``ts``, ``value``). ``start``/``end`` are
    optional ISO timestamps to sub-slice the stored ~30-day window.
    """
    clauses = ["site_no = ?", "parameter = ?"]
    params: list = [site_no, parameter]
    if start:
        clauses.append("ts >= ?"); params.append(str(start))
    if end:
        clauses.append("ts <= ?"); params.append(str(end))
    where = " AND ".join(clauses)
    return con.execute(
        f"SELECT ts, value, qualifiers FROM instantaneous_values "
        f"WHERE {where} ORDER BY ts", params).df()


def iv_site_list(con, *, parameter: str, states: list[str] | None = None):
    """Sites that have 15-minute data for a parameter (with reading counts)."""
    clauses = ["iv.parameter = ?"]
    params: list = [parameter]
    if states:
        placeholders = ",".join("?" for _ in states)
        clauses.append(f"iv.state IN ({placeholders})")
        params.extend(states)
    where = " AND ".join(clauses)
    return con.execute(
        f"""SELECT iv.site_no, iv.state, s.station_nm,
                   count(*) AS n_readings,
                   min(iv.ts) AS first_ts, max(iv.ts) AS last_ts
            FROM instantaneous_values iv
            LEFT JOIN sites s ON iv.site_no = s.site_no
            WHERE {where}
            GROUP BY iv.site_no, iv.state, s.station_nm
            ORDER BY n_readings DESC""", params).df()


def iv_coverage(con):
    """Per-parameter 15-minute coverage summary (sites, readings, window)."""
    return con.execute(
        """SELECT parameter,
                  count(DISTINCT site_no) AS n_sites,
                  count(*) AS n_readings,
                  min(ts) AS window_start, max(ts) AS window_end
           FROM instantaneous_values
           GROUP BY parameter ORDER BY n_readings DESC""").df()
