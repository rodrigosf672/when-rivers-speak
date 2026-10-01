"""Offline tests for the River Pulse payload encoding (no downloads, no database)."""
from __future__ import annotations

import datetime as dt
import json
import threading
import zlib

import numpy as np
import pandas as pd
import pytest

from rivers.pulse import assets, memo, settings
from rivers.pulse.flow import (
    Discharge,
    _flow_codes,
    _pack_flow_year,
    hold_short_gaps,
    pack_flow_year,
    year_cells,
)
from rivers.pulse.packing import _checked, _row_delta, pack_sections, payload_kb


def _sections(frame: pd.DataFrame) -> dict:
    out = {}
    for row in frame.to_dict("records"):
        raw = zlib.decompress(row["blob"]) if row["codec"] == "zlib" else row["blob"]
        if row["name"] == "meta":
            out["meta"] = json.loads(raw)
        elif row["dtype"] == "bytes":
            out[row["name"]] = raw
        else:
            out[row["name"]] = np.frombuffer(raw, dtype=np.dtype(row["dtype"])).reshape(
                json.loads(row["shape"]))
    return out


def test_pack_sections_round_trip():
    grid = np.arange(12, dtype=np.uint16).reshape(3, 4)
    frame = pack_sections({"answer": 42}, grid=grid, image=b"\x89PNG")
    decoded = _sections(frame)
    assert decoded["meta"] == {"answer": 42}
    assert np.array_equal(decoded["grid"], grid)
    assert decoded["image"] == b"\x89PNG"
    assert payload_kb(frame) > 0


def test_row_delta_wraps_mod_2_16():
    grid = np.array([[0, 65535, 3, 1000], [7, 7, 0, 65535]], dtype=np.uint16)
    delta = _row_delta(grid)
    restored = (np.cumsum(delta.astype(np.int64), axis=1) & 0xFFFF).astype(np.uint16)
    assert np.array_equal(restored, grid)


def test_payload_limit_is_enforced():
    noise = np.random.default_rng(0).integers(0, 255, 2_000_000, dtype=np.uint8)
    with pytest.raises(ValueError, match="per-cell limit"):
        _checked(pack_sections({}, noise=noise), "noise")


def test_flow_codes_and_year_packing():
    dates = pd.date_range("2021-01-01", "2022-12-31", freq="D")
    cfs = np.full((2, len(dates)), 10.0, np.float32)
    cfs[0, :30] = 80.0     # 8x normal: top code
    cfs[1, 40:50] = np.nan  # a gap: code 0
    discharge = Discharge(cfs=cfs, start=dt.date(2021, 1, 1), dates=dates)
    flow = _flow_codes(discharge, "median")
    assert flow.codes[0, 0] == settings.FLOW_LEVELS
    assert flow.codes[0, 100] == 1 + round((settings.FLOW_LEVELS - 1) / 2)
    assert (flow.codes[1, 40:50] == 0).all()

    frame = _pack_flow_year(discharge, flow, 2022)
    decoded = _sections(frame)
    assert decoded["meta"] == {"year": 2022, "day0": 365, "days": 365, "slots": 2}
    codes = (np.cumsum(decoded["codes"].astype(np.int64), axis=1) & 0xFF).astype(np.uint8)
    assert np.array_equal(codes, flow.codes[:, 365:])


@pytest.mark.parametrize("year", [2015, 2019, 2020, 2023, 2030])
def test_years_outside_the_data_pack_empty(year):
    dates = pd.date_range("2021-01-01", "2022-12-31", freq="D")
    discharge = Discharge(cfs=np.full((3, len(dates)), 5.0, np.float32),
                          start=dt.date(2021, 1, 1), dates=dates)
    decoded = _sections(_pack_flow_year(discharge, _flow_codes(discharge, "median"), year))
    assert decoded["meta"]["days"] == 0
    assert decoded["codes"].shape == (3, 0)


def test_year_cells_are_positional():
    memo.clear()
    dates = pd.date_range("2025-03-01", "2026-02-10", freq="D")
    discharge = Discharge(cfs=np.full((2, len(dates)), 5.0, np.float32),
                          start=dt.date(2025, 3, 1), dates=dates)
    flow = _flow_codes(discharge, "median")
    assert year_cells(discharge) == [(2025, "river_pulse_year_0"), (2026, "river_pulse_year_1")]
    first = _sections(pack_flow_year(discharge, flow, 0))["meta"]
    assert (first["year"], first["day0"], first["days"]) == (2025, 0, 306)
    last = _sections(pack_flow_year(discharge, flow, 1))["meta"]
    assert (last["year"], last["day0"], last["days"]) == (2026, 306, 41)
    spare = _sections(pack_flow_year(discharge, flow, settings.YEAR_CELLS - 1))["meta"]
    assert spare["days"] == 0
    memo.clear()


def test_hold_short_gaps_only_fills_interior_gaps():
    q = np.full((1, 40), 1.0, np.float32)
    q[0, :3] = np.nan       # before the first reading: stays empty
    q[0, 5:15] = np.nan     # 10-day gap: held
    q[0, 20:31] = np.nan    # 11-day gap: stays empty
    q[0, 37:] = np.nan      # after the last reading: stays empty
    held = hold_short_gaps(q, max_gap=10)
    assert np.isnan(held[0, :3]).all()
    assert not np.isnan(held[0, 5:15]).any()
    assert np.isnan(held[0, 20:31]).all()
    assert np.isnan(held[0, 37:]).all()


def test_concurrent_ensure_builds_once(tmp_path, monkeypatch):
    calls = []

    def fake_build_into(con, staging, log):
        calls.append(staging)
        (staging / assets.MANIFEST).write_text(json.dumps({"version": settings.ASSET_VERSION}))

    monkeypatch.setattr(assets, "_build_into", fake_build_into)
    monkeypatch.setattr(assets, "load_assets", lambda asset_dir: asset_dir)
    memo.clear()
    target = tmp_path / "pulse"
    results = []
    threads = [threading.Thread(
        target=lambda: results.append(assets.ensure_assets(None, asset_dir=target, log=lambda m: None)))
        for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert results == [target] * 6
    assert sorted(p.name for p in tmp_path.iterdir()) == [".pulse.lock", "pulse"]
    memo.clear()


def test_memo_builds_once_across_threads():
    memo.clear()
    calls = []

    def build():
        calls.append(1)
        return object()

    results = []
    threads = [threading.Thread(target=lambda: results.append(memo.memo("k", build)))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert all(r is results[0] for r in results)
    memo.clear()
