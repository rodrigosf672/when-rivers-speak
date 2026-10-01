"""Map projection for River Pulse: CONUS plus Alaska and Hawaii insets.

``MapRegions`` projects lon/lat (and line geometries) of each region into the
shared map plane, and knows each inset's frame.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import settings


def split_nation(path: Path) -> dict:
    """Split the Census nation outline into CONUS, Alaska, and Hawaii parts."""
    import pyogrio.raw
    import shapely

    _, _, geometry, _ = pyogrio.raw.read(f"/vsizip/{path}/{path.stem}.shp")
    parts = {"conus": [], "ak": [], "hi": []}
    for polygon in shapely.from_wkb(geometry[0]).geoms:
        x, y = polygon.representative_point().coords[0]
        if y > 50 or x > 170:
            parts["ak"].append(polygon)
        elif x < -150 and y < 30:
            parts["hi"].append(polygon)
        elif y > 23 and x > -130:
            parts["conus"].append(polygon)
    return {key: shapely.MultiPolygon(polys) for key, polys in parts.items()}


@dataclass
class MapRegions:
    """Per-region transformers, inset offsets and frames, and the US outline."""

    regions: dict
    outline: dict

    @classmethod
    def from_outline(cls, nation_zip: Path) -> MapRegions:
        import pyproj
        import shapely

        outline = split_nation(nation_zip)
        regions = {}
        for key, spec in settings.REGIONS.items():
            fwd = pyproj.Transformer.from_crs("EPSG:4326", spec["crs"], always_xy=True)
            inv = pyproj.Transformer.from_crs(spec["crs"], "EPSG:4326", always_xy=True)
            projected = shapely.transform(
                outline[key], lambda c, t=fwd: np.column_stack(t.transform(c[:, 0], c[:, 1])))
            x0, y0, x1, y1 = np.array(projected.bounds) + np.array([-40e3, -40e3, 40e3, 40e3])
            s = spec["scale"]
            if spec["anchor"] is None:
                offset = (0.0, 0.0)
            else:
                offset = (spec["anchor"][0] - x0 * s, spec["anchor"][1] - y0 * s)
            regions[key] = {
                **spec, "fwd": fwd, "inv": inv, "offset": offset,
                "frame": (float(x0 * s + offset[0]), float(y0 * s + offset[1]),
                          float(x1 * s + offset[0]), float(y1 * s + offset[1])),
            }
        return cls(regions=regions, outline=outline)

    def __getitem__(self, key: str) -> dict:
        return self.regions[key]

    def lonlat_to_map(self, key: str, lon, lat):
        """Project lon/lat of one region into shared map metres."""
        region = self.regions[key]
        x, y = region["fwd"].transform(np.asarray(lon, float), np.asarray(lat, float))
        return (np.asarray(x) * region["scale"] + region["offset"][0],
                np.asarray(y) * region["scale"] + region["offset"][1])

    def lines_to_map(self, key: str, lines):
        import shapely

        region = self.regions[key]

        def _project(coords):
            x, y = region["fwd"].transform(coords[:, 0], coords[:, 1])
            return np.column_stack([np.asarray(x) * region["scale"] + region["offset"][0],
                                    np.asarray(y) * region["scale"] + region["offset"][1]])

        return shapely.transform(lines, _project)


def region_of(lon, lat, state=None) -> np.ndarray:
    """Which map region a lon/lat (or USGS state code) belongs to."""
    lon = np.asarray(lon, float)
    lat = np.asarray(lat, float)
    key = np.where((lat > 51) & ((lon < -129) | (lon > 170)), "ak", "conus")
    key = np.where((lat < 23) & (lon < -150), "hi", key)
    if state is not None:
        state = np.asarray(state)
        key = np.where(state == "AK", "ak", np.where(state == "HI", "hi", key))
    return key
