"""Payloads for the Studio ``pulse`` view.

Each payload is one Arrow table of named, zlib-compressed typed arrays (see
``pack_sections``), decoded by the view's ``src/lib/payload.ts``. Studio sends
each defining cell's values in one response of at most 1 MB, so every payload
is checked against ``settings.PAYLOAD_LIMIT_KB`` and large ones live in their
own notebook cells.
"""
from __future__ import annotations

import json
import zlib

import numpy as np
import pandas as pd

from . import memo, settings
from .assets import PulseAssets
from .terrain import SURFACE_CLASSES


def pack_sections(meta: dict, **arrays) -> pd.DataFrame:
    """Pack typed arrays as zlib blobs in one Arrow table for the view.

    Each row carries a name, a NumPy dtype string, a shape, and the bytes.
    Pre-encoded bytes pass through with codec "raw".
    """
    rows = []
    for name, value in arrays.items():
        if isinstance(value, bytes):
            rows.append({"name": name, "dtype": "bytes", "shape": "[]", "codec": "raw",
                         "blob": value})
            continue
        array = np.ascontiguousarray(value)
        rows.append({"name": name, "dtype": array.dtype.str, "shape": json.dumps(array.shape),
                     "codec": "zlib", "blob": zlib.compress(array.tobytes(), 9)})
    rows.append({"name": "meta", "dtype": "json", "shape": "[]", "codec": "raw",
                 "blob": json.dumps(meta).encode()})
    return pd.DataFrame(rows)


def payload_kb(frame: pd.DataFrame) -> float:
    return sum(len(b) for b in frame["blob"]) / 1024


def _checked(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    size = payload_kb(frame)
    if size >= settings.PAYLOAD_LIMIT_KB:
        raise ValueError(f"{label} payload is {size:.0f} KB; Studio's per-cell limit is "
                         f"{settings.PAYLOAD_LIMIT_KB} KB")
    return frame


def _row_delta(grid: np.ndarray) -> np.ndarray:
    """Delta-code rows (mod 2^16); browsers cannot read 16-bit PNG."""
    return np.diff(grid.astype(np.int32), axis=1, prepend=0).astype(np.uint16)


def pack_water(assets: PulseAssets) -> pd.DataFrame:
    """Surface classes, shore distance (for foam) and sea depth (for colour)."""
    return memo.memo(("water", id(assets)), lambda: _pack_water(assets))


def _pack_water(assets: PulseAssets) -> pd.DataFrame:
    return _checked(pack_sections(
        {"surface_classes": SURFACE_CLASSES, "shore_units_per_px": 8,
         "max_depth_m": int(assets.depth_half_m.max())},
        surface=assets.surface,
        shore=assets.shore,
        depth_delta=_row_delta(assets.depth_half_m),
    ), "terrain water")


def pack_relief_band(assets: PulseAssets, band: int) -> pd.DataFrame:
    """One horizontal band of the land relief (display metres)."""
    return memo.memo(("relief", id(assets), band), lambda: _pack_relief_band(assets, band))


def _pack_relief_band(assets: PulseAssets, band: int) -> pd.DataFrame:
    relief = assets.relief_m
    rows = np.array_split(np.arange(relief.shape[0]), settings.RELIEF_BANDS)[band]
    block = relief[rows[0]:rows[-1] + 1]
    return _checked(pack_sections(
        {"band": band, "bands": settings.RELIEF_BANDS, "row0": int(rows[0]),
         "rows": len(rows), "width": int(relief.shape[1]), "height": int(relief.shape[0]),
         "max_m": int(relief.max())},
        height_delta=_row_delta(block),
    ), f"relief band {band}")


def pack_rivers(assets: PulseAssets, label: str) -> pd.DataFrame:
    """Reaches as quantized, per-reach delta-coded vertices ("major" or "minor").

    Sections: ``nverts`` (u2), ``dx``/``dy`` (u2, first vertex absolute then
    deltas mod 2^16), ``width`` (u1, 40 * (log10 mean m3/s + 2)), ``slot``
    (u2, 65535 when no gauge reaches it), ``dist_dn`` (f4, map km from the
    reach's downstream end to the sea, used to animate flow downstream). Big
    rivers come last so they draw on top.
    """
    return memo.memo(("rivers", id(assets), label), lambda: _pack_rivers(assets, label))


def _pack_rivers(assets: PulseAssets, label: str) -> pd.DataFrame:
    import shapely

    network = assets.network
    major = network.upland_km2 >= settings.MAJOR_UPLAND_KM2
    subset = network[major if label == "major" else ~major]
    subset = subset.iloc[np.argsort(subset.dis_cms.to_numpy(), kind="stable")]
    lines = assets.lines[subset.index.to_numpy()]
    counts = shapely.get_num_coordinates(lines)
    coords = shapely.get_coordinates(lines)
    xmin, ymin, xmax, _ = settings.MAP_EXTENT
    quantum = (xmax - xmin) / 65535
    q = np.round((coords - [xmin, ymin]) / quantum).astype(np.int64)
    delta = np.diff(q, axis=0, prepend=np.zeros((1, 2), np.int64))
    starts = np.cumsum(counts) - counts
    delta[starts] = q[starts]
    scale = subset.region.map({k: v["scale"] for k, v in settings.REGIONS.items()}).to_numpy()
    width = np.clip(np.round(40 * (np.log10(np.maximum(subset.dis_cms.to_numpy(), 0.01)) + 2)),
                    0, 255)
    return _checked(pack_sections(
        {"label": label, "reaches": len(subset), "vertices": len(coords),
         "quantum_m": quantum, "origin_m": [xmin, ymin]},
        nverts=counts.astype(np.uint16),
        dx=(delta[:, 0] & 0xFFFF).astype(np.uint16),
        dy=(delta[:, 1] & 0xFFFF).astype(np.uint16),
        width=width.astype(np.uint8),
        slot=np.where(subset.slot >= 0, subset.slot, 65535).astype(np.uint16),
        dist_dn=(subset.dist_dn_km.fillna(0).to_numpy() * scale).astype(np.float32),
    ), f"rivers {label}")
