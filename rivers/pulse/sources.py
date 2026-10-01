"""On-demand downloads of the public geography behind the River Pulse map.

Every file is fetched once into ``settings.RAW_DIR`` / ``settings.TILE_DIR``
(inside the gitignored cache) and reused afterwards.
"""
from __future__ import annotations

import math
import os
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from ..config import USER_AGENT
from . import settings


def fetch_cached(url: str, path: Path) -> Path:
    """Download ``url`` to ``path`` once; later calls reuse the file."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=300) as response:
            data = response.read()
        partial = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.part")
        partial.write_bytes(data)
        os.replace(partial, path)
    return path


def raw_sources() -> dict[str, Path]:
    """Fetch (once) HydroRIVERS, the Census US outline and Natural Earth lakes."""
    return {key: fetch_cached(url, settings.RAW_DIR / url.rsplit("/", 1)[-1])
            for key, url in settings.SOURCES.items()}


def terrarium_mosaic(lonlat: tuple, zoom: int) -> dict:
    """Fetch (once) and stitch Terrarium elevation tiles covering a lon/lat box."""
    from PIL import Image

    west, south, east, north = lonlat
    n = 2**zoom

    def _tx(lon):
        return int((lon + 180) / 360 * n)

    def _ty(lat):
        return int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)

    xs = range(_tx(west), min(_tx(east), n - 1) + 1)
    ys = range(_ty(north), _ty(south) + 1)

    def _get(xy):
        x, y = xy
        return fetch_cached(settings.TERRARIUM_URL.format(z=zoom, x=x, y=y),
                            settings.TILE_DIR / f"{zoom}/{x}/{y}.png")

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(_get, [(x, y) for y in ys for x in xs]))
    elevation = np.zeros((len(ys) * 256, len(xs) * 256), np.float32)
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            rgb = np.asarray(Image.open(settings.TILE_DIR / f"{zoom}/{x}/{y}.png").convert("RGB"),
                             np.float32)
            elevation[j * 256:(j + 1) * 256, i * 256:(i + 1) * 256] = (
                rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768
            )
    return {"elevation": elevation, "x0": xs.start, "y0": ys.start, "zoom": zoom,
            "lonlat": lonlat}


def sample_mosaic(mosaic: dict, lon, lat, outside: float = -50.0) -> np.ndarray:
    """Bilinear elevation at lon/lat; points outside the mosaic get ``outside``."""
    lon = np.asarray(lon, float)
    lat = np.asarray(lat, float)
    lon = np.where(lon > 180, lon - 360, lon)
    n = 2 ** mosaic["zoom"] * 256
    px = (lon + 180) / 360 * n - mosaic["x0"] * 256 - 0.5
    py = ((1 - np.arcsinh(np.tan(np.radians(np.clip(lat, -85, 85)))) / np.pi) / 2 * n
          - mosaic["y0"] * 256 - 0.5)
    grid = mosaic["elevation"]
    h, w = grid.shape
    west, south, east, north = mosaic["lonlat"]
    inside = (lon >= west) & (lon <= east) & (lat >= south) & (lat <= north)
    px = np.clip(px, 0, w - 1.001)
    py = np.clip(py, 0, h - 1.001)
    i = px.astype(int)
    j = py.astype(int)
    fx = px - i
    fy = py - j
    value = (grid[j, i] * (1 - fx) * (1 - fy) + grid[j, i + 1] * fx * (1 - fy)
             + grid[j + 1, i] * (1 - fx) * fy + grid[j + 1, i + 1] * fx * fy)
    return np.where(inside, value, outside).astype(np.float32)
