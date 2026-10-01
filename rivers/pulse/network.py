"""River network for River Pulse: reaches, gauges snapped to them, and spread.

HydroRIVERS covers CONUS and Alaska; Hawaii is traced from terrain. Each USGS
discharge gauge is snapped to a nearby reach whose modelled mean flow matches
the gauge's, then its signal spreads along the network to ungauged reaches.
"""
from __future__ import annotations

import heapq
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import settings
from .regions import MapRegions, region_of
from .sources import sample_mosaic, terrarium_mosaic
from .terrain import TerrainGrid

REACH_COLUMNS = ["reach_id", "next_down", "length_km", "dist_dn_km", "upland_km2", "endorheic",
                 "dis_cms", "region", "sea_outlet"]


def load_hydrorivers(regions: MapRegions, terrain: TerrainGrid,
                     rivers_na: Path, rivers_ar: Path) -> tuple[pd.DataFrame, np.ndarray]:
    """HydroRIVERS reaches in or next to the US, with map-coordinate lines.

    Keeps reaches whose midpoint is on US land, or on coastal water right next
    to it (deltas, estuaries), and flags sea outlets: reaches whose NEXT_DOWN
    chain ends in the ocean near US soil.
    """
    import pyarrow as pa
    import pyogrio
    import shapely
    from scipy import ndimage

    columns = ["HYRIV_ID", "NEXT_DOWN", "LENGTH_KM", "DIST_DN_KM", "UPLAND_SKM", "ENDORHEIC",
               "DIS_AV_CMS"]
    reads = ((rivers_na, (-126.0, 22.0, -60.0, 50.5)), (rivers_na, (-141.0, 54.0, -129.0, 61.0)),
             (rivers_ar, (-180.0, 51.0, -129.0, 72.0)))
    tables = []
    for archive, bbox in reads:
        layer = archive.stem.removesuffix("_shp")
        _, table = pyogrio.read_arrow(f"/vsizip/{archive}/{archive.stem}/{layer}.shp",
                                      columns=columns, bbox=bbox,
                                      where=f"UPLAND_SKM >= {settings.MIN_UPLAND_KM2}")
        tables.append(table)
    table = pa.concat_tables(tables)
    _, first = np.unique(table["HYRIV_ID"].to_numpy(), return_index=True)
    table = table.take(np.sort(first))
    everything = pd.DataFrame({c: table[c].to_numpy() for c in columns})
    lines = shapely.from_wkb(table["wkb_geometry"].to_numpy(zero_copy_only=False))
    mid = shapely.line_interpolate_point(lines, 0.5, normalized=True)
    everything["region"] = region_of(shapely.get_x(mid), shapely.get_y(mid))
    map_lines = np.empty(len(lines), object)
    for key in ("conus", "ak"):
        selected = (everything["region"] == key).to_numpy()
        map_lines[selected] = regions.lines_to_map(key, lines[selected])

    xmin, _, _, ymax = settings.MAP_EXTENT
    height, width = terrain.surface.shape
    map_mid = shapely.line_interpolate_point(map_lines, 0.5, normalized=True)
    col = np.clip(((shapely.get_x(map_mid) - xmin) / terrain.px_m).astype(int), 0, width - 1)
    row = np.clip(((ymax - shapely.get_y(map_mid)) / terrain.px_m).astype(int), 0, height - 1)
    px_to_us = ndimage.distance_transform_edt(terrain.surface != 2)
    surface = terrain.surface[row, col]
    distance = px_to_us[row, col]
    keep = (surface == 2) | ((surface == 0) & (distance <= 3))

    next_of = dict(zip(everything["HYRIV_ID"], everything["NEXT_DOWN"]))
    row_of = {rid: i for i, rid in enumerate(everything["HYRIV_ID"])}
    us_ids = set(everything["HYRIV_ID"][keep])
    endorheic = everything["ENDORHEIC"].to_numpy()

    def drains_to_us_sea(start_id: int) -> bool:
        current = start_id
        for _ in range(100_000):
            nxt = next_of.get(current)
            if nxt is None:
                return False
            if nxt == 0:
                i = row_of[current]
                return endorheic[i] == 0 and distance[i] <= 12
            current = nxt
        return False

    kept = everything[keep].reset_index(drop=True)
    kept["sea_outlet"] = [(nd not in us_ids) and drains_to_us_sea(rid)
                          for rid, nd in zip(kept["HYRIV_ID"], kept["NEXT_DOWN"])]
    reaches = kept.rename(columns={
        "HYRIV_ID": "reach_id", "NEXT_DOWN": "next_down", "LENGTH_KM": "length_km",
        "DIST_DN_KM": "dist_dn_km", "UPLAND_SKM": "upland_km2", "ENDORHEIC": "endorheic",
        "DIS_AV_CMS": "dis_cms",
    })
    return reaches[REACH_COLUMNS], map_lines[keep]


def trace_hawaii(regions: MapRegions) -> tuple[pd.DataFrame, np.ndarray]:
    """Trace Hawaii's streams from a 250 m terrain grid (HydroRIVERS has no Hawaii).

    Priority-flood fill (Barnes et al. 2014), steepest-descent flow directions,
    accumulated area, then channels cut into reaches at confluences.
    """
    import shapely
    from scipy import ndimage

    res_m, min_km2, runoff = 250.0, 4.0, 0.035  # runoff: m3/s per km2, a rough statewide mean
    hi = regions["hi"]
    s, (ox, oy) = hi["scale"], hi["offset"]
    fx0, fy0, fx1, fy1 = hi["frame"]
    xs = np.arange((fx0 - ox) / s, (fx1 - ox) / s, res_m) + res_m / 2
    ys = np.arange((fy1 - oy) / s, (fy0 - oy) / s, -res_m) - res_m / 2
    gx, gy = np.meshgrid(xs, ys)
    lon, lat = hi["inv"].transform(gx, gy)
    z = sample_mosaic(terrarium_mosaic((-160.5, 18.7, -154.6, 22.4), 9), lon, lat)
    land = z > 0.5
    land[[0, -1], :] = False
    land[:, [0, -1]] = False
    h, w = z.shape
    n = h * w

    filled = np.where(land, z, -1.0).astype(np.float64).ravel()
    seen = (~land).ravel().copy()
    coast = (land & ndimage.binary_dilation(~land, structure=np.ones((3, 3), bool))).ravel()
    heap = [(float(filled[i]), int(i)) for i in np.flatnonzero(coast)]
    heapq.heapify(heap)
    seen[coast] = True
    offsets = (-w - 1, -w, -w + 1, -1, 1, w - 1, w, w + 1)
    while heap:
        e, i = heapq.heappop(heap)
        for o in offsets:
            j = i + o
            if seen[j]:
                continue
            seen[j] = True
            if filled[j] <= e:
                filled[j] = e + 1e-3
            heapq.heappush(heap, (filled[j], j))

    surface = filled.reshape(h, w)
    best = np.full((h, w), -np.inf)
    receiver = np.full((h, w), -1, np.int64)
    rows, cols = np.indices((h, w))
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            neighbour = np.roll(np.roll(surface, -dr, 0), -dc, 1)
            drop = (surface - neighbour) / math.hypot(dr, dc)
            better = land & (drop > best)
            best[better] = drop[better]
            receiver[better] = ((rows + dr) * w + (cols + dc))[better]
    receiver = receiver.ravel()
    land_flat = land.ravel()

    acc = np.where(land_flat, 1.0, 0.0).tolist()
    receivers = receiver.tolist()
    for i in np.flatnonzero(land_flat)[np.argsort(-filled[land_flat])].tolist():
        r = receivers[i]
        if r >= 0 and land_flat[r]:
            acc[r] += acc[i]
    acc = np.array(acc)
    cell_km2 = (res_m / 1000) ** 2
    channel = land_flat & (acc * cell_km2 >= min_km2)
    to_land = (receiver >= 0) & np.take(land_flat, np.maximum(receiver, 0))
    donors = np.bincount(receiver[channel & to_land], minlength=n)

    starts = np.flatnonzero(channel & ((donors == 0) | (donors >= 2)))
    start_reach = {int(c): k for k, c in enumerate(starts)}
    paths, next_reach = [], []
    for c in starts.tolist():
        path = [c]
        current = receivers[c]
        nxt = -1
        while True:
            if current < 0 or not land_flat[current]:
                if current >= 0:
                    path.append(current)  # end on the first sea cell so the line meets the coast
                break
            path.append(current)
            if donors[current] >= 2:
                nxt = start_reach[current]
                break
            current = receivers[current]
        paths.append(path)
        next_reach.append(nxt)

    ids = 950_000_000 + np.arange(len(paths))
    px_x, px_y = gx.ravel(), gy.ravel()
    lines, upland, length = [], [], []
    for path in paths:
        coords = np.column_stack([px_x[path] * s + ox, px_y[path] * s + oy])
        if len(coords) > 2:  # one Chaikin pass softens the D8 zig-zag
            q = 0.75 * coords[:-1] + 0.25 * coords[1:]
            r = 0.25 * coords[:-1] + 0.75 * coords[1:]
            coords = np.vstack([coords[:1], np.column_stack([q, r]).reshape(-1, 2), coords[-1:]])
        lines.append(shapely.LineString(coords))
        last_land = [c for c in path if land_flat[c]][-1]
        upland.append(acc[last_land] * cell_km2)
        length.append(shapely.length(lines[-1]) / s / 1000)

    dist_dn = np.full(len(paths), np.nan)
    pending = [k for k, nx in enumerate(next_reach) if nx < 0]
    for k in pending:
        dist_dn[k] = 0.0
    upstream: dict[int, list[int]] = {}
    for k, nx in enumerate(next_reach):
        if nx >= 0:
            upstream.setdefault(nx, []).append(k)
    while pending:
        k = pending.pop()
        for u in upstream.get(k, []):
            dist_dn[u] = dist_dn[k] + length[k]
            pending.append(u)

    reaches = pd.DataFrame({
        "reach_id": ids,
        "next_down": [int(ids[nx]) if nx >= 0 else 0 for nx in next_reach],
        "length_km": length,
        "dist_dn_km": dist_dn,
        "upland_km2": upland,
        "endorheic": 0,
        "dis_cms": np.array(upland) * runoff,
        "region": "hi",
        "sea_outlet": [nx < 0 for nx in next_reach],
    })
    return reaches[REACH_COLUMNS], np.array(lines, dtype=object)


def gauge_stats(con) -> pd.DataFrame:
    """USGS discharge gauges with at least a year of non-negative daily values."""
    gauges = con.execute("""
        with stats as (
            select site_no, avg(value) as q_mean_cfs, count(*) as n_days
            from daily_values
            where parameter = 'discharge' and value >= 0
            group by site_no
        )
        select s.site_no, s.q_mean_cfs, s.n_days, t.station_nm, t.latitude, t.longitude, t.state
        from stats s join (select distinct on (site_no) * from sites) t using (site_no)
        where s.n_days >= 365 and s.q_mean_cfs > 0 and t.latitude is not null
        order by s.site_no
    """).df()
    return gauges.reset_index(drop=True)


def snap_gauges(regions: MapRegions, reaches: pd.DataFrame, lines: np.ndarray,
                gauges: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Snap each gauge to a nearby reach whose modelled mean flow matches it.

    A creek gauge near a confluence keeps its creek. One gauge per reach: the
    best-matching one speaks for it. Returns (gauges with map x/y, snaps).
    """
    import shapely

    gauges = gauges.copy()
    gauges["region"] = region_of(gauges.longitude, gauges.latitude, gauges.state)
    gauges["x"] = np.nan
    gauges["y"] = np.nan
    for key in ("conus", "ak", "hi"):
        selected = gauges.region == key
        x, y = regions.lonlat_to_map(key, gauges.longitude[selected], gauges.latitude[selected])
        gauges.loc[selected, "x"] = x
        gauges.loc[selected, "y"] = y

    scale = gauges.region.map({k: v["scale"] for k, v in regions.regions.items()}).to_numpy()
    points = shapely.points(gauges.x, gauges.y)
    gi, ri = shapely.STRtree(lines).query(points, predicate="dwithin",
                                          distance=settings.SNAP_KM * 1000)
    dist_km = shapely.distance(points[gi], lines[ri]) / 1000 / scale[gi]
    q_cms = gauges.q_mean_cfs.to_numpy()[gi] * settings.CFS_TO_CMS
    mismatch = np.abs(np.log2(np.maximum(q_cms, 1e-4)
                              / np.maximum(reaches.dis_cms.to_numpy()[ri], 1e-4)))
    hawaii = gauges.region.to_numpy()[gi] == "hi"
    ok = (dist_km <= settings.SNAP_KM) & ((mismatch <= settings.SNAP_MAX_OCTAVES)
                                          | (hawaii & (dist_km <= 1.5)))
    score = dist_km / 1.5 + np.where(hawaii, 0.0, mismatch)
    candidates = pd.DataFrame({"gauge": gi, "reach": ri, "dist_km": dist_km,
                               "mismatch": mismatch, "score": score})[ok]
    best = candidates.sort_values("score").drop_duplicates("gauge")
    snaps = best.sort_values("score").drop_duplicates("reach").sort_values("gauge")
    return gauges, snaps.reset_index(drop=True)


def spread(reaches: pd.DataFrame, gauges: pd.DataFrame,
           snaps: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Spread each gauge's signal along the network; return (network, slots).

    Steps cost their length plus a penalty for jumping between rivers of very
    different size, so a tributary without a gauge follows its own kind, not
    the main stem. Slots are the gauges that colour at least one reach.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra

    n = len(reaches)
    index = {rid: i for i, rid in enumerate(reaches.reach_id)}
    down = np.array([index.get(nd, -1) for nd in reaches.next_down])
    a = np.flatnonzero(down >= 0)
    b = down[a]
    dis = np.maximum(reaches.dis_cms.to_numpy(), 1e-3)
    length = reaches.length_km.to_numpy()
    cost = (length[a] + length[b]) / 2 + 150 * np.abs(np.log(dis[a] / dis[b]))
    graph = coo_matrix((cost, (a, b)), shape=(n, n)).tocsr()
    sources = snaps.reach.to_numpy()
    dist, _, src = dijkstra(graph, directed=False, indices=sources, min_only=True,
                            return_predecessors=True, limit=settings.SPREAD_LIMIT_KM)

    reached = np.isfinite(dist)
    slot_of_source = {reach: k for k, reach in enumerate(sources)}
    gauge_slot = np.full(n, -1)
    gauge_slot[reached] = [slot_of_source[s] for s in src[reached]]
    used = np.unique(gauge_slot[reached])
    renumber = np.full(len(sources), -1)
    renumber[used] = np.arange(len(used))
    network = reaches.assign(
        slot=np.where(gauge_slot >= 0, renumber[np.maximum(gauge_slot, 0)], -1),
        spread_km=np.where(reached, dist, np.nan),
    )
    slots = snaps.iloc[used].reset_index(drop=True).join(gauges, on="gauge")
    slots["slot"] = np.arange(len(slots))
    slots["reach_dis_cms"] = network.dis_cms.to_numpy()[slots.reach]
    return network, slots
