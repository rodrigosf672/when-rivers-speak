"""Parse raw USGS payloads into tidy pandas frames and Parquet partitions.

Two input shapes are handled:

* **Site RDB** (tab-delimited with ``#`` comments and a format-spec line) ->
  a ``sites`` frame.
* **Daily / instantaneous JSON** (WaterML-in-JSON) -> long-format value frames.

Output is written as Parquet partitioned by ``state`` / ``parameter`` / ``year``
so the DuckDB build and incremental updates only touch the slices that change.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd

from .config import PARAM_BY_CODE, PARQUET_DIR

# --------------------------------------------------------------------------- #
# Column schemas (kept explicit so empty frames still have the right columns)
# --------------------------------------------------------------------------- #
SITE_COLUMNS = [
    "site_no", "station_nm", "site_tp_cd", "latitude", "longitude",
    "coord_acy_cd", "datum", "altitude", "huc_cd", "state",
]
DAILY_COLUMNS = [
    "site_no", "parameter", "param_code", "date", "value", "qualifiers",
    "state", "year",
]
LATEST_COLUMNS = [
    "site_no", "parameter", "param_code", "datetime", "value", "qualifiers", "state",
]
INSTANTANEOUS_COLUMNS = [
    "site_no", "parameter", "param_code", "datetime", "value", "qualifiers",
    "state", "date",
]


# --------------------------------------------------------------------------- #
# Sites (RDB)
# --------------------------------------------------------------------------- #
def parse_sites_rdb(rdb_text: str, state: str) -> pd.DataFrame:
    """Parse USGS Site-Service RDB text into a tidy sites frame."""
    if not rdb_text or not rdb_text.strip():
        return pd.DataFrame(columns=SITE_COLUMNS)

    # Drop comment lines; the first non-comment line is the header, the second
    # is a format spec (e.g. "5s", "15s") we skip.
    lines = [ln for ln in rdb_text.splitlines() if not ln.startswith("#")]
    if len(lines) < 3:
        return pd.DataFrame(columns=SITE_COLUMNS)
    body = "\n".join([lines[0]] + lines[2:])
    raw = pd.read_csv(io.StringIO(body), sep="\t", dtype=str)

    df = pd.DataFrame()
    df["site_no"] = raw.get("site_no")
    df["station_nm"] = raw.get("station_nm")
    df["site_tp_cd"] = raw.get("site_tp_cd")
    df["latitude"] = pd.to_numeric(raw.get("dec_lat_va"), errors="coerce")
    df["longitude"] = pd.to_numeric(raw.get("dec_long_va"), errors="coerce")
    df["coord_acy_cd"] = raw.get("coord_acy_cd")
    df["datum"] = raw.get("dec_coord_datum_cd")
    df["altitude"] = pd.to_numeric(raw.get("alt_va"), errors="coerce")
    df["huc_cd"] = raw.get("huc_cd")
    df["state"] = state.upper()

    # Keep only rows with usable coordinates (needed for mapping).
    df = df.dropna(subset=["latitude", "longitude"])
    df = df.drop_duplicates(subset=["site_no"]).reset_index(drop=True)
    return df[SITE_COLUMNS]


# --------------------------------------------------------------------------- #
# Values (JSON, shared shape between /dv and /iv)
# --------------------------------------------------------------------------- #
def _iter_series(json_text: str):
    """Yield (site_no, param_code, [value-records]) from a WaterML/JSON blob."""
    if not json_text or not json_text.strip():
        return
    doc = json.loads(json_text)
    for series in doc.get("value", {}).get("timeSeries", []):
        src = series.get("sourceInfo", {})
        codes = src.get("siteCode") or [{}]
        site_no = codes[0].get("value")
        var = series.get("variable", {})
        vcodes = var.get("variableCode") or [{}]
        param_code = vcodes[0].get("value")
        if not site_no or not param_code:
            continue
        # A series can carry multiple method blocks; concatenate their values.
        records: list[dict] = []
        for block in series.get("values", []):
            records.extend(block.get("value", []))
        yield site_no, param_code, records


def parse_daily_json(json_text: str, state: str) -> pd.DataFrame:
    """Parse daily-values JSON into a long tidy frame (one row per site/day)."""
    rows: list[dict] = []
    for site_no, param_code, records in _iter_series(json_text):
        param = PARAM_BY_CODE.get(param_code)
        pkey = param.key if param else param_code
        for rec in records:
            val = pd.to_numeric(rec.get("value"), errors="coerce")
            dt = str(rec.get("dateTime", ""))[:10]
            if not dt or pd.isna(val):
                continue
            rows.append({
                "site_no": site_no, "parameter": pkey, "param_code": param_code,
                "date": dt, "value": float(val),
                "qualifiers": ",".join(rec.get("qualifiers", []) or []),
                "state": state.upper(), "year": int(dt[:4]),
            })
    if not rows:
        return pd.DataFrame(columns=DAILY_COLUMNS)
    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["site_no", "parameter", "date"])
    return df[DAILY_COLUMNS].reset_index(drop=True)


def parse_latest_json(json_text: str, state: str) -> pd.DataFrame:
    """Parse instantaneous-values JSON and keep the most recent row per site."""
    rows: list[dict] = []
    for site_no, param_code, records in _iter_series(json_text):
        param = PARAM_BY_CODE.get(param_code)
        pkey = param.key if param else param_code
        best = None
        for rec in records:
            val = pd.to_numeric(rec.get("value"), errors="coerce")
            dt = rec.get("dateTime")
            if not dt or pd.isna(val):
                continue
            if best is None or dt > best[0]:
                best = (dt, float(val), ",".join(rec.get("qualifiers", []) or []))
        if best is not None:
            rows.append({
                "site_no": site_no, "parameter": pkey, "param_code": param_code,
                "datetime": best[0], "value": best[1], "qualifiers": best[2],
                "state": state.upper(),
            })
    if not rows:
        return pd.DataFrame(columns=LATEST_COLUMNS)
    return pd.DataFrame(rows)[LATEST_COLUMNS].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Parquet writers (partitioned)
# --------------------------------------------------------------------------- #
def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_sites(df: pd.DataFrame, base: Path | None = None) -> Path | None:
    """Write the sites table to ``<base>/sites/state=<XX>/sites.parquet``."""
    if df.empty:
        return None
    base = base or PARQUET_DIR
    state = str(df["state"].iloc[0])
    out = _ensure(base / "sites" / f"state={state}") / "sites.parquet"
    df.to_parquet(out, index=False)
    return out


def write_daily(df: pd.DataFrame, base: Path | None = None) -> list[Path]:
    """Write daily values partitioned by state/parameter/year."""
    if df.empty:
        return []
    base = base or PARQUET_DIR
    written: list[Path] = []
    for (state, param, year), part in df.groupby(["state", "parameter", "year"]):
        d = _ensure(base / "daily" / f"state={state}" /
                    f"parameter={param}" / f"year={year}")
        out = d / "daily.parquet"
        part.to_parquet(out, index=False)
        written.append(out)
    return written


def write_latest(df: pd.DataFrame, base: Path | None = None) -> list[Path]:
    """Write latest observations partitioned by state/parameter."""
    if df.empty:
        return []
    base = base or PARQUET_DIR
    written: list[Path] = []
    for (state, param), part in df.groupby(["state", "parameter"]):
        d = _ensure(base / "latest" / f"state={state}" / f"parameter={param}")
        out = d / "latest.parquet"
        part.to_parquet(out, index=False)
        written.append(out)
    return written


def parse_instantaneous_json(json_text: str, state: str) -> pd.DataFrame:
    """Parse instantaneous-values JSON keeping *all* 15-minute records.

    Unlike ``parse_latest_json`` (which keeps only the most recent reading per
    site), this retains the full high-frequency series so a trailing window of
    15-minute data can be stored for real-time-monitoring views.
    """
    rows: list[dict] = []
    for site_no, param_code, records in _iter_series(json_text):
        param = PARAM_BY_CODE.get(param_code)
        pkey = param.key if param else param_code
        for rec in records:
            val = pd.to_numeric(rec.get("value"), errors="coerce")
            dt = rec.get("dateTime")
            if not dt or pd.isna(val):
                continue
            rows.append({
                "site_no": site_no, "parameter": pkey, "param_code": param_code,
                "datetime": dt, "value": float(val),
                "qualifiers": ",".join(rec.get("qualifiers", []) or []),
                "state": state.upper(),
            })
    if not rows:
        return pd.DataFrame(columns=INSTANTANEOUS_COLUMNS)
    df = pd.DataFrame(rows)
    # date partition key (UTC-naive calendar date of the reading)
    df["date"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce").dt.date.astype(str)
    return df[INSTANTANEOUS_COLUMNS].reset_index(drop=True)


def write_instantaneous(df: pd.DataFrame, base: Path | None = None) -> list[Path]:
    """Write 15-minute instantaneous values partitioned by state/parameter.

    One Parquet file per state+parameter holds the trailing window; the writer
    overwrites it so a scheduled refresh replaces the slice atomically.
    """
    if df.empty:
        return []
    base = base or PARQUET_DIR
    written: list[Path] = []
    for (state, param), part in df.groupby(["state", "parameter"]):
        d = _ensure(base / "instantaneous" / f"state={state}" / f"parameter={param}")
        out = d / "iv.parquet"
        part.sort_values("datetime").to_parquet(out, index=False)
        written.append(out)
    return written
