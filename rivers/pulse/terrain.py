"""Terrain and water on the shared River Pulse map grid.

Every grid pixel belongs to exactly one frame: an inset (Alaska, Hawaii) inside
its box, CONUS everywhere else. Surface classes are 0 sea, 1 other land,
2 US land, 3 lake.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import settings
from .regions import MapRegions, region_of
from .sources import sample_mosaic, terrarium_mosaic

SURFACE_CLASSES = {"0": "sea", "1": "other land", "2": "US land", "3": "lake"}


@dataclass
class TerrainGrid:
    elevation: np.ndarray  # metres, Terrarium (negative at sea)
    frame: np.ndarray      # 0 conus, 1 ak, 2 hi
    surface: np.ndarray    # surface classes
    px_m: float            # map metres per pixel

    def display_relief_m(self, regions: MapRegions) -> np.ndarray:
        """Land height in display metres (insets scaled with their frame), zero on water."""
        scale = np.choose(self.frame, [1.0, regions["ak"]["scale"], regions["hi"]["scale"]])
        land = np.isin(self.surface, (1, 2))
        relief = np.where(land, np.maximum(self.elevation, 0) * scale, 0)
        return np.clip(np.round(relief), 0, 65535).astype(np.uint16)

    def shore_eighths(self) -> np.ndarray:
        """Distance from each water pixel to the nearest land, in 1/8 px (for foam)."""
        from scipy import ndimage

        land = np.isin(self.surface, (1, 2))
        distance = ndimage.distance_transform_edt(~land)
        return np.clip(np.round(distance * 8), 0, 255).astype(np.uint8)

    def sea_depth_half_m(self) -> np.ndarray:
        """Sea depth in metres at half resolution (for shallow-to-deep colour)."""
        from PIL import Image

        depth = np.where(self.surface == 0, np.maximum(-self.elevation, 0), 0).astype(np.float32)
        width = depth.shape[1] // 2
        height = round(width * depth.shape[0] / depth.shape[1])
        half = np.asarray(Image.fromarray(depth, "F").resize((width, height), Image.BOX))
        return np.clip(np.round(half), 0, 65535).astype(np.uint16)


def build_terrain(regions: MapRegions, lakes_zip: Path) -> TerrainGrid:
    """Sample Terrarium elevation for every frame and rasterize land, lakes and sea."""
    import pyogrio.raw
    import shapely
    from PIL import Image, ImageDraw

    xmin, ymin, xmax, ymax = settings.MAP_EXTENT
    width = settings.TERRAIN_WIDTH
    height = round(width * (ymax - ymin) / (xmax - xmin))
    px_m = (xmax - xmin) / width
    mx, my = np.meshgrid(xmin + (np.arange(width) + 0.5) * px_m,
                         ymax - (np.arange(height) + 0.5) * px_m)

    frame = np.zeros((height, width), np.uint8)
    elevation = np.zeros((height, width), np.float32)
    in_inset = np.zeros((height, width), bool)
    for code, key in ((1, "ak"), (2, "hi"), (0, "conus")):
        region = regions[key]
        if key == "conus":
            selected = ~in_inset
        else:
            fx0, fy0, fx1, fy1 = region["frame"]
            selected = (mx >= fx0) & (mx <= fx1) & (my >= fy0) & (my <= fy1)
            in_inset |= selected
        frame[selected] = code
        lon, lat = region["inv"].transform((mx[selected] - region["offset"][0]) / region["scale"],
                                           (my[selected] - region["offset"][1]) / region["scale"])
        elevation[selected] = sample_mosaic(terrarium_mosaic(region["lonlat"], region["zoom"]),
                                            lon, lat)

    def to_px(key, coords):
        x, y = regions.lonlat_to_map(key, coords[:, 0], coords[:, 1])
        return list(zip(((x - xmin) / px_m).tolist(), ((ymax - y) / px_m).tolist()))

    image = Image.fromarray(np.where(elevation > 0, 1, 0).astype(np.uint8), "L")
    draw = ImageDraw.Draw(image)
    for key in ("conus", "ak", "hi"):
        for polygon in regions.outline[key].geoms:
            draw.polygon(to_px(key, np.asarray(polygon.exterior.coords)), fill=2)
            for hole in polygon.interiors:
                draw.polygon(to_px(key, np.asarray(hole.coords)), fill=0)
    # Large lakes (Great Lakes, Great Salt Lake, Okeechobee, ...) as water.
    _, _, lake_wkb, lake_fields = pyogrio.raw.read(f"/vsizip/{lakes_zip}/ne_10m_lakes.shp",
                                                   columns=["scalerank"])
    for geom, rank in zip(shapely.from_wkb(lake_wkb), lake_fields[0]):
        if rank > 3:
            continue
        for polygon in getattr(geom, "geoms", [geom]):
            cx, cy = polygon.representative_point().coords[0]
            if -180 < cx < -60 and 22 < cy < 72:
                draw.polygon(to_px(region_of(cx, cy).item(), np.asarray(polygon.exterior.coords)),
                             fill=3)
    return TerrainGrid(elevation=elevation, frame=frame, surface=np.asarray(image).copy(),
                       px_m=float(px_m))
