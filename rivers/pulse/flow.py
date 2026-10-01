"""Daily flow for River Pulse: discharge per gauge slot, compared with normal.

``discharge_matrix`` reads daily discharge for every slot gauge from the app's
DuckDB ``daily_values`` view; ``flow_codes`` turns it into colour codes against
a chosen baseline; ``pack_flow_year`` and ``summary`` build the view payloads.
"""
from __future__ import annotations

import datetime as dt
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import DUCKDB_PATH, PARQUET_DIR
from . import memo, settings
from .assets import PulseAssets
from .packing import _checked, pack_sections


def _stamp(path) -> int:
    return path.stat().st_mtime_ns if path.exists() else 0


@dataclass
class Discharge:
    cfs: np.ndarray            # slots x days, short gaps held, NaN where missing
    start: dt.date
    dates: pd.DatetimeIndex

    @property
    def days(self) -> int:
        return len(self.dates)


@dataclass
class FlowCodes:
    codes: np.ndarray       # uint8 slots x days; 1..FLOW_LEVELS, 0 = no reading
    log2_ratio: np.ndarray  # float32 slots x days, NaN = no reading
    baseline: str


def discharge_matrix(con, assets: PulseAssets) -> Discharge:
    """Daily discharge (ft3/s) for every slot gauge: rows are slots, columns days."""
    key = ("discharge", str(DUCKDB_PATH), _stamp(DUCKDB_PATH), str(PARQUET_DIR), id(assets))
    return memo.memo(key, lambda: _discharge_matrix(con, assets))


def _discharge_matrix(con, assets: PulseAssets) -> Discharge:
    slots = assets.slots[["site_no", "slot"]]
    con.register("pulse_slot_sites", slots)
    try:
        start, end = con.execute("""
            select min(date_d), max(date_d) from daily_values where parameter = 'discharge'
        """).fetchone()
        # Only the years the view animates (one cell per year).
        start = max(start, dt.date(end.year - settings.YEAR_CELLS + 1, 1, 1))
        obs = con.execute(f"""
            select s.slot, datediff('day', DATE '{start}', d.date_d) as day, d.value
            from daily_values d join pulse_slot_sites s using (site_no)
            where d.parameter = 'discharge' and d.value >= 0 and d.date_d >= DATE '{start}'
        """).df()
    finally:
        con.unregister("pulse_slot_sites")
    dates = pd.date_range(start, end, freq="D")
    q = np.full((len(slots), len(dates)), np.nan, np.float32)
    q[obs.slot.to_numpy(), obs.day.to_numpy()] = obs.value.to_numpy()
    return Discharge(cfs=hold_short_gaps(q), start=start, dates=dates)


def hold_short_gaps(q: np.ndarray, max_gap: int = settings.HOLD_GAP_DAYS) -> np.ndarray:
    """Fill interior gaps of at most ``max_gap`` days from the neighbouring readings.

    Longer gaps, and days before a gauge's first or after its last reading, stay NaN.
    """
    missing = np.isnan(q)
    day = np.arange(q.shape[1], dtype=np.int32)
    prev = np.maximum.accumulate(np.where(missing, -1, day), axis=1)
    after = np.minimum.accumulate(np.where(missing, q.shape[1], day)[:, ::-1], axis=1)[:, ::-1]
    short = missing & (prev >= 0) & (after < q.shape[1]) & (after - prev - 1 <= max_gap)
    half = (max_gap + 1) // 2
    held = pd.DataFrame(q).ffill(axis=1, limit=half).bfill(axis=1, limit=half).to_numpy(np.float32)
    return np.where(short, held, q).astype(np.float32)


def flow_codes(discharge: Discharge, baseline: str) -> FlowCodes:
    """Colour codes for flow vs normal.

    "Normal" is each gauge's median over the whole record ("median"), or the
    median of the same calendar window (plus or minus 15 days) across all years
    ("seasonal").
    """
    return memo.memo(("flow", id(discharge), baseline), lambda: _flow_codes(discharge, baseline))


def _flow_codes(discharge: Discharge, baseline: str) -> FlowCodes:
    cfs = discharge.cfs
    overall = np.nanmedian(cfs, axis=1)
    overall = np.where(overall > 0, overall, np.nanmean(cfs, axis=1))
    if baseline == "seasonal":
        doy = np.minimum(discharge.dates.dayofyear.to_numpy() - 1, 364)
        by_doy = np.empty((cfs.shape[0], 365), np.float32)
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # gauges silent all window
            for d in range(365):
                gap = np.abs(doy - d)
                window = np.minimum(gap, 365 - gap) <= 15
                by_doy[:, d] = np.nanmedian(cfs[:, window], axis=1)
        normal = by_doy[:, doy]
        normal = np.where(normal > 0, normal, overall[:, None])
    else:
        normal = np.broadcast_to(overall[:, None], cfs.shape)
    log2_ratio = np.log2(np.maximum(cfs, 1e-6) / normal).astype(np.float32)
    r = settings.LOG2_RANGE
    scaled = (np.clip(log2_ratio, -r, r) + r) / (2 * r)
    codes = np.where(np.isnan(cfs), 0,
                     1 + np.round(scaled * (settings.FLOW_LEVELS - 1))).astype(np.uint8)
    return FlowCodes(codes=codes, log2_ratio=log2_ratio, baseline=baseline)


def year_cells(discharge: Discharge) -> list[tuple[int, str]]:
    """(year, app.py value name) for each animated year, oldest first."""
    first, last = discharge.start.year, discharge.dates[-1].year
    if last - first + 1 > settings.YEAR_CELLS:
        raise ValueError(f"{last - first + 1} years of discharge, but app.py has "
                         f"{settings.YEAR_CELLS} year cells")
    return [(year, f"river_pulse_year_{k}") for k, year in enumerate(range(first, last + 1))]


def pack_flow_year(discharge: Discharge, flow: FlowCodes, position: int) -> pd.DataFrame:
    """Colour codes for the ``position``-th animated year, delta-coded along days.

    Positions past the last year with data give an empty chunk.
    """
    cells = year_cells(discharge)
    year = cells[position][0] if position < len(cells) else discharge.dates[-1].year + 1
    return memo.memo(("flow_year", id(flow), year), lambda: _pack_flow_year(discharge, flow, year))


def _pack_flow_year(discharge: Discharge, flow: FlowCodes, year: int) -> pd.DataFrame:
    d0 = min(max(0, (dt.date(year, 1, 1) - discharge.start).days), discharge.days)
    d1 = max(d0, min(discharge.days, (dt.date(year + 1, 1, 1) - discharge.start).days))
    block = flow.codes[:, d0:d1]
    delta = np.diff(block.astype(np.int16), axis=1, prepend=0).astype(np.uint8)
    return _checked(pack_sections(
        {"year": year, "day0": d0, "days": max(0, d1 - d0), "slots": int(flow.codes.shape[0])},
        codes=delta,
    ), f"flow {year}")


def summary(assets: PulseAssets, discharge: Discharge,
            flow: FlowCodes) -> tuple[dict, pd.DataFrame]:
    """Map metadata and the daily summary (sea discharge, high/low shares)."""
    return memo.memo(("summary", id(assets), id(flow)), lambda: _summary(assets, discharge, flow))


def _summary(assets: PulseAssets, discharge: Discharge,
             flow: FlowCodes) -> tuple[dict, pd.DataFrame]:
    network, slots = assets.network, assets.slots
    # Discharge to the sea: every US sea outlet, scaled from the gauge that
    # speaks for it by the ratio of modelled mean flow (outlet / gauge reach).
    # Outlets with no gauge, or a gauge silent that day, count at their modelled mean.
    outlets = network[network.sea_outlet & (network.dis_cms >= 1.0)]
    dis = outlets.dis_cms.to_numpy()
    slot = outlets.slot.to_numpy()
    gauged = slot >= 0
    factor = np.clip(dis[gauged] / slots.reach_dis_cms.to_numpy()[slot[gauged]], 0.2, 5.0)
    q = discharge.cfs[slot[gauged]] * settings.CFS_TO_CMS * factor[:, None]
    sea_cms = np.where(np.isnan(q), dis[gauged][:, None], q).sum(axis=0) + dis[~gauged].sum()

    reporting = ~np.isnan(flow.log2_ratio)
    n = np.maximum(reporting.sum(axis=0), 1)
    daily = pd.DataFrame({
        "day": np.arange(discharge.days, dtype=np.int32),
        "date": discharge.dates.strftime("%Y-%m-%d"),
        "sea_cms": sea_cms.astype(np.float32),
        "high_share": ((flow.log2_ratio >= 1).sum(axis=0) / n).astype(np.float32),
        "low_share": ((flow.log2_ratio <= -1).sum(axis=0) / n).astype(np.float32),
        "reporting": reporting.sum(axis=0).astype(np.int32),
    })

    label = {v: k for k, v in settings.BASELINES.items()}[flow.baseline]
    start, end = discharge.dates[0].date(), discharge.dates[-1].date()
    manifest = assets.manifest
    meta = {
        "title": "The United States' River Pulse",
        "extent_km": [v / 1000 for v in manifest["extent_m"]],
        "insets": [{"key": i["key"], "label": i["label"], "frame_km": [v / 1000 for v in i["frame_m"]]}
                   for i in manifest["insets"]],
        "date_start": str(start),
        "date_end": str(end),
        "days": int(discharge.days),
        "years": [{"year": y, "cell": cell} for y, cell in year_cells(discharge)],
        "flow_levels": settings.FLOW_LEVELS,
        "log2_range": settings.LOG2_RANGE,
        "baseline": flow.baseline,
        "baseline_label": label,
        "counts": {
            "reaches": len(network),
            "gauges": len(slots),
            "gauges_total": int(manifest["counts"]["gauges_total"]),
            "sea_outlets": len(outlets),
            "river_km": float(network.length_km.sum()),
        },
        "sea_gauged_share": float(dis[gauged].sum() / dis.sum()),
        "sources": [
            f"Discharge: USGS daily values at {len(slots):,} gauges, each spread along its river",
            *settings.SOURCE_LINES,
            f"Colour: each gauge vs {label.lower()}",
        ],
    }
    return meta, daily


def gauge_points(assets: PulseAssets) -> pd.DataFrame:
    """One row per gauge slot, for hover tooltips in the view."""
    return memo.memo(("points", id(assets)), lambda: _gauge_points(assets))


def _gauge_points(assets: PulseAssets) -> pd.DataFrame:
    slots = assets.slots
    return pd.DataFrame({
        "slot": slots.slot.astype(np.int32),
        "site_no": slots.site_no,
        "name": slots.station_nm.fillna(""),
        "x_km": (slots.x / 1000).astype(np.float32),
        "y_km": (slots.y / 1000).astype(np.float32),
        "mean_cms": (slots.q_mean_cfs * settings.CFS_TO_CMS).astype(np.float32),
    })
