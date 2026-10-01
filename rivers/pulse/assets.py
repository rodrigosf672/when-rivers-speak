"""Build, save and load the derived River Pulse map layers.

``ensure_assets(con)`` is the entry point for the notebook: it loads the layers
from ``settings.ASSET_DIR`` and, on first use (or with ``force=True``),
downloads the public geography and derives them. ``scripts/build_pulse_assets.py``
runs the same build from the command line.

Saved files:

- ``terrain.npz``: land relief (display metres), surface classes, shore
  distance (1/8 px) and half-resolution sea depth.
- ``reaches.parquet``: every drawn reach with its gauge slot and simplified
  map-coordinate line (WKB).
- ``slots.parquet``: the gauges that colour the map, one row per slot.
- ``manifest.json``: version, map extent, inset frames and counts.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import memo, settings

MANIFEST = "manifest.json"
_build_lock = threading.Lock()


@dataclass
class PulseAssets:
    relief_m: np.ndarray
    surface: np.ndarray
    shore: np.ndarray
    depth_half_m: np.ndarray
    network: pd.DataFrame  # reaches with slot, spread_km, sea_outlet, ...
    lines: np.ndarray      # shapely LineStrings (simplified, map metres), aligned with network
    slots: pd.DataFrame
    manifest: dict


def build_assets(con, asset_dir: Path | None = None, log=print) -> Path:
    """Download the public geography (once) and derive the map layers."""
    asset_dir = asset_dir or settings.ASSET_DIR
    with _exclusive(asset_dir):
        return _build(con, asset_dir, log)


def _build(con, asset_dir: Path, log) -> Path:
    # Build next to the target and swap it in at the end, so readers never see
    # a half-written set. Creating it first also fails fast on a read-only store.
    staging = asset_dir.with_name(f".{asset_dir.name}.building-{os.getpid()}")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        _build_into(con, staging, log)
        _swap_in(staging, asset_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    log(f"River Pulse: wrote map layers to {asset_dir}")
    return asset_dir


def _build_into(con, asset_dir: Path, log) -> None:
    import shapely

    from .network import gauge_stats, load_hydrorivers, snap_gauges, spread, trace_hawaii
    from .regions import MapRegions
    from .sources import raw_sources
    from .terrain import SURFACE_CLASSES, build_terrain

    log("River Pulse: fetching public geography (first run downloads about 110 MB)")
    raw = raw_sources()
    regions = MapRegions.from_outline(raw["nation"])
    log("River Pulse: sampling terrain and water")
    terrain = build_terrain(regions, raw["lakes"])
    log("River Pulse: loading HydroRIVERS and tracing Hawaii's streams")
    hydro, hydro_lines = load_hydrorivers(regions, terrain, raw["rivers_na"], raw["rivers_ar"])
    hawaii, hawaii_lines = trace_hawaii(regions)
    reaches = pd.concat([hydro, hawaii], ignore_index=True)
    lines = np.concatenate([hydro_lines, hawaii_lines])
    log("River Pulse: snapping gauges and spreading them along the network")
    gauges, snaps = snap_gauges(regions, reaches, lines, gauge_stats(con))
    network, slots = spread(reaches, gauges, snaps)

    np.savez_compressed(
        asset_dir / "terrain.npz",
        relief_m=terrain.display_relief_m(regions),
        surface=terrain.surface,
        shore=terrain.shore_eighths(),
        depth_half_m=terrain.sea_depth_half_m(),
    )
    simple = shapely.simplify(lines, settings.SIMPLIFY_M)
    network.assign(geometry=shapely.to_wkb(simple)).to_parquet(asset_dir / "reaches.parquet")
    slots[["slot", "site_no", "station_nm", "state", "region", "x", "y", "q_mean_cfs",
           "reach", "reach_dis_cms"]].to_parquet(asset_dir / "slots.parquet")
    manifest = {
        "version": settings.ASSET_VERSION,
        "built": dt.date.today().isoformat(),
        "extent_m": list(settings.MAP_EXTENT),
        "px_m": terrain.px_m,
        "insets": [{"key": key, "label": label, "frame_m": list(regions[key]["frame"])}
                   for key, label in settings.INSET_LABELS.items()],
        "surface_classes": SURFACE_CLASSES,
        "counts": {
            "reaches": len(network),
            "hydrorivers_reaches": len(hydro),
            "hawaii_reaches": len(hawaii),
            "gauges_total": len(gauges),
            "gauges_snapped": len(snaps),
            "slots": len(slots),
        },
        "sources": settings.SOURCES,
    }
    (asset_dir / MANIFEST).write_text(json.dumps(manifest, indent=2))


def _swap_in(staging: Path, asset_dir: Path) -> None:
    old = asset_dir.with_name(f".{asset_dir.name}.old-{os.getpid()}")
    if asset_dir.exists():
        os.replace(asset_dir, old)
    os.replace(staging, asset_dir)
    shutil.rmtree(old, ignore_errors=True)


@contextlib.contextmanager
def _exclusive(asset_dir: Path):
    """Serialize builds across sessions (threads) and processes."""
    with _build_lock:
        try:
            import fcntl
        except ImportError:  # Windows: threads only
            yield
            return
        asset_dir.parent.mkdir(parents=True, exist_ok=True)
        with open(asset_dir.with_name(f".{asset_dir.name}.lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def load_assets(asset_dir: Path | None = None) -> PulseAssets:
    import shapely

    asset_dir = asset_dir or settings.ASSET_DIR
    manifest = json.loads((asset_dir / MANIFEST).read_text())
    terrain = np.load(asset_dir / "terrain.npz")
    reaches = pd.read_parquet(asset_dir / "reaches.parquet")
    lines = shapely.from_wkb(reaches.pop("geometry").to_numpy())
    return PulseAssets(
        relief_m=terrain["relief_m"],
        surface=terrain["surface"],
        shore=terrain["shore"],
        depth_half_m=terrain["depth_half_m"],
        network=reaches,
        lines=lines,
        slots=pd.read_parquet(asset_dir / "slots.parquet"),
        manifest=manifest,
    )


def assets_ready(asset_dir: Path | None = None) -> bool:
    path = (asset_dir or settings.ASSET_DIR) / MANIFEST
    if not path.exists():
        return False
    return json.loads(path.read_text()).get("version") == settings.ASSET_VERSION


def ensure_assets(con, force: bool = False, asset_dir: Path | None = None,
                  log=print) -> PulseAssets:
    """Load the map layers, building them first if missing, outdated or forced.

    Loaded layers are shared by every session in the process.
    """
    asset_dir = asset_dir or settings.ASSET_DIR
    if force or not assets_ready(asset_dir):
        with _exclusive(asset_dir):
            # Another session or process may have built them while we waited.
            if force or not assets_ready(asset_dir):
                _build(con, asset_dir, log)
    # A rebuild changes the manifest stamp, so the new layers get a new memo key.
    # Older memo entries stay put: derived results are keyed by id() of objects
    # the memo holds, which must not be freed and reused.
    stamp = (asset_dir / MANIFEST).stat().st_mtime_ns
    return memo.memo(("assets", str(asset_dir), stamp), lambda: load_assets(asset_dir))
