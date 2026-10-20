from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
from pathlib import Path
import warnings
import zipfile

import geopandas as gpd
import neatnet
import networkx as nx
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString, Point

from .osm import grade_anchors, read_osm
from .topology import build_graph, coordinate, lines, node_lines, validate


@contextmanager
def small_network_support():
    # neatnet 0.1.6's KDE needs multiple faces, even with a fixed threshold.
    import neatnet.artifacts as artifacts
    import neatnet.simplify as simplify
    from esda.shape import minimum_bounding_circle_ratio

    original_class, original_get = artifacts.FaceArtifacts, simplify.get_artifacts

    class SmallFaces(original_class):
        def __init__(self, frame, **kwargs):
            faces = gpd.GeoSeries(list(shapely.get_parts(shapely.polygonize([
                shapely.union_all(frame.geometry.array)
            ]))), crs=frame.crs)
            if len(faces) < 3:
                self.polygons = gpd.GeoDataFrame(geometry=faces)
                self.polygons["face_artifact_index"] = (
                    np.log(minimum_bounding_circle_ratio(faces) * faces.area)
                    if len(faces) else pd.Series(dtype=float)
                )
                self.threshold = None
            else:
                super().__init__(frame, **kwargs)

    def get_artifacts(streets, **kwargs):
        faces = shapely.polygonize([shapely.union_all(streets.geometry.array)])
        if faces.is_empty:
            return gpd.GeoDataFrame(geometry=[], crs=streets.crs), kwargs.get("threshold", 7)
        return original_get(streets, **kwargs)

    artifacts.FaceArtifacts, simplify.get_artifacts = SmallFaces, get_artifacts
    try:
        yield
    finally:
        artifacts.FaceArtifacts, simplify.get_artifacts = original_class, original_get


def merge_open_carriageways(source, max_width=40):
    geometries = list(source.geometry)
    candidates = source[source.oneway.isin(["yes", "1", "-1"])].copy()
    candidates["corridor"] = candidates["name"].where(candidates["name"] != "", candidates.ref)
    candidates = candidates[candidates.corridor != ""]
    for _, group in candidates.groupby(["corridor", "highway"]):
        paths = lines(shapely.line_merge(shapely.union_all(group.geometry.array)))
        if len(paths) != 2:
            continue
        a, b = paths
        if min(a.length, b.length) < 100 or min(a.length, b.length) / max(a.length, b.length) < 0.8:
            continue
        if a.is_ring or b.is_ring:
            continue
        direct = max(math.dist(a.coords[0], b.coords[0]), math.dist(a.coords[-1], b.coords[-1]))
        reverse = max(math.dist(a.coords[0], b.coords[-1]), math.dist(a.coords[-1], b.coords[0]))
        if min(direct, reverse) > max_width or a.hausdorff_distance(b) > max_width:
            continue
        distances = np.linspace(0, 1, max(20, int(max(a.length, b.length) / 5)))
        center = LineString([
            ((a.interpolate(t, normalized=True).x + b.interpolate(1 - t if reverse < direct else t, normalized=True).x) / 2,
             (a.interpolate(t, normalized=True).y + b.interpolate(1 - t if reverse < direct else t, normalized=True).y) / 2)
            for t in distances
        ])
        old = shapely.union_all([a, b])
        cookie = old.buffer(0.01)
        remaining = []
        for geometry in geometries:
            if not geometry.intersects(cookie):
                remaining.append(geometry)
                continue
            for part in lines(geometry.difference(cookie)):
                coords = list(part.coords)
                for end in (0, -1):
                    point = Point(coords[end])
                    if point.distance(old) < 0.02:
                        target = center.interpolate(center.project(point))
                        coords[end] = target.coords[0]
                remaining.append(LineString(coords))
        geometries = remaining + [center]
    return geometries


def collapse_roundabouts(geometries, source, anchor_points):
    rings = source[source.junction.isin(["roundabout", "circular"])]
    if rings.empty:
        return geometries
    polygons = shapely.get_parts(shapely.polygonize([shapely.union_all(rings.geometry.array)]))
    for polygon in polygons:
        if polygon.area > 100_000:
            continue
        cookie, center = polygon.buffer(2), polygon.centroid
        remaining = []
        for geometry in geometries:
            if not geometry.intersects(cookie):
                remaining.append(geometry)
                continue
            for part in lines(geometry.difference(cookie)):
                coords = list(part.coords)
                if cookie.boundary.distance(Point(coords[0])) < 0.01:
                    coords.insert(0, center.coords[0])
                if cookie.boundary.distance(Point(coords[-1])) < 0.01:
                    coords.append(center.coords[0])
                remaining.append(LineString(coords))
        for point in anchor_points:
            if cookie.covers(point) and point.distance(center) > 0.001:
                remaining.append(LineString([center, point]))
        # Keep an isolated ring if there are no legs to represent its center.
        geometries = remaining if remaining else geometries
    return geometries


def restore_anchors(geometries, source, anchors, level):
    geometries = lines(shapely.union_all(geometries, grid_size=0.001))
    points = [(node_id, Point(xy)) for node_id, (xy, levels) in anchors.items() if level in levels]
    if not points:
        return geometries, []
    tree = shapely.STRtree(geometries)
    source_graph = None
    review = []
    for node_id, point in points:
        nearest = geometries[int(tree.nearest(point))]
        distance = nearest.distance(point)
        if distance <= 0.002:
            continue
        if distance <= 2:
            geometries.append(LineString([point, nearest.interpolate(nearest.project(point))]))
            continue
        if source_graph is None:
            source_graph = nx.Graph()
            for row in source.itertuples():
                coords = list(row.geometry.coords)
                for a, b in zip(coords, coords[1:]):
                    ka, kb = coordinate(a), coordinate(b)
                    if ka != kb:
                        source_graph.add_edge(ka, kb, weight=math.dist(ka, kb))
        origin = coordinate(point.coords[0])
        destination = None
        if origin in source_graph:
            lengths, paths = nx.single_source_dijkstra(source_graph, origin, cutoff=2000)
            for candidate, _ in sorted(lengths.items(), key=lambda pair: pair[1]):
                if candidate == origin:
                    continue
                target = geometries[int(tree.nearest(Point(candidate)))]
                if target.distance(Point(candidate)) <= 2:
                    destination = candidate
                    break
        if destination is None:
            review.append({"osm_node_id": node_id, "level": int(level),
                           "distance_m": distance, "geometry": point})
        else:
            route = paths[destination]
            geometries.append(LineString(route))
            target = geometries[int(tree.nearest(Point(destination)))]
            snap = target.interpolate(target.project(Point(destination)))
            if snap.distance(Point(destination)) > 0.001:
                geometries.append(LineString([destination, snap.coords[0]]))
    return node_lines(geometries, [point for _, point in points]), review


@dataclass
class Result:
    roads: gpd.GeoDataFrame
    nodes: gpd.GeoDataFrame
    source: gpd.GeoDataFrame
    review: gpd.GeoDataFrame
    stats: dict


def process(extract, tolerance=8, progress=lambda message: None):
    if not 1 <= tolerance <= 20:
        raise ValueError("交叉点合并距离应在 1–20 米之间。")
    source, buildings = extract.roads, extract.buildings
    anchors = grade_anchors(source)
    strata, review = {}, []
    for level, subset in source.groupby("level", sort=True):
        category = "地面道路" if level == 0 else "桥隧与分层道路"
        progress(f"合并{category} · {len(subset):,} 条")
        geometries = merge_open_carriageways(subset)
        frame = gpd.GeoDataFrame(geometry=geometries, crs=source.crs)
        with small_network_support(), warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            simplified = neatnet.neatify(
                frame, exclusion_mask=buildings if level == 0 and len(buildings) else None,
                artifact_threshold=7, consolidation_tolerance=tolerance,
                min_dangle_length=0, max_segment_length=2, simplification_factor=1, n_loops=2,
            )
            simplified = neatnet.consolidate_nodes(simplified, tolerance=tolerance, preserve_ends=True)
        anchor_points = [Point(xy) for xy, levels in anchors.values() if level in levels]
        geometries = collapse_roundabouts(list(simplified.geometry), subset, anchor_points)
        if not geometries:
            raise ValueError(f"层级 {level} 的道路为空。")
        strata[int(level)], missing = restore_anchors(geometries, subset, anchors, int(level))
        review.extend(missing)
    progress("构建交叉点与道路连接")
    roads, nodes, components = build_graph(strata, anchors, source.crs)
    validate(roads, nodes)
    review_frame = (gpd.GeoDataFrame(review, crs=source.crs) if review else
                    gpd.GeoDataFrame({"osm_node_id": [], "level": [], "distance_m": []}, geometry=[], crs=source.crs))
    stats = {
        "source_roads": len(source), "roads": len(roads), "nodes": len(nodes),
        "intersections": int((nodes.degree >= 3).sum()), "components": components,
        "length_km": round(roads.length.sum() / 1000, 2),
        "grade_anchors": len(anchors), "review_points": len(review),
        "incomplete_ways_skipped": extract.skipped, "tolerance_m": tolerance,
        "crs": source.crs.to_string(), "directed": False,
        "method": "neatnet 0.1.6; automatic geometry simplification",
        "attribution": "© OpenStreetMap contributors",
        "data_license": "https://opendatacommons.org/licenses/odbl/1-0/",
    }
    return Result(roads, nodes, source.drop(columns=["node_ids"]), review_frame, stats)


def export(result, directory):
    from .viewer import write_viewer
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError(f"结果目录不为空：{directory}")
    directory.mkdir(parents=True, exist_ok=True)
    gpkg = directory / "street-skeleton.gpkg"
    frames = {
        "roads": result.roads, "nodes": result.nodes,
        "intersections": result.nodes[result.nodes.degree >= 3],
        "review_points": result.review,
    }
    for layer, frame in frames.items():
        frame.to_file(gpkg, layer=layer, driver="GPKG")
        frame.to_crs(4326).to_file(directory / f"{layer}.geojson", driver="GeoJSON")
    (directory / "metadata.json").write_text(json.dumps(result.stats, ensure_ascii=False, indent=2), encoding="utf-8")
    write_viewer(result, directory / "map.html")
    from .cable import write_network
    write_network(result, directory / "cable-network.json")
    files = list(directory.iterdir())
    with zipfile.ZipFile(directory / "street-skeleton.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            if path.is_file():
                archive.write(path, path.name)


def run(input_path, output_path, tolerance=8, progress=print):
    progress("读取 OSM 城市数据")
    extract = read_osm(input_path)
    result = process(extract, tolerance, progress)
    result.stats["city_name"] = Path(input_path).name.removesuffix(".pbf").removesuffix(".osm")
    progress("保存地图与结果")
    export(result, output_path)
    return result
