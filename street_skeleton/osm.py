from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import osmium
from shapely.geometry import LineString, shape


STREETS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "living_street", "pedestrian", "road", "motorway_link",
    "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}


def keep_road(tags):
    if tags.get("indoor", "").lower() in {"yes", "true", "1"} or tags.get("area") == "yes":
        return False
    highway = tags.get("highway")
    if highway in STREETS:
        return True
    if highway == "service":
        return tags.get("service") not in {
            "parking_aisle", "driveway", "drive-through", "emergency_access",
        } and tags.get("access") not in {"private", "no"}
    return highway == "footway" and bool(tags.get("name") or tags.get("name:zh")) and tags.get("footway") not in {
        "sidewalk", "crossing", "access_aisle",
    }


def road_level(tags):
    try:
        level = int(float(tags.get("layer", "0").split(";")[0]))
    except (ValueError, TypeError):
        level = 0
    if level == 0:
        if tags.get("bridge", "no") not in {"", "no", "false", "0"}:
            return 1
        if tags.get("tunnel", "no") not in {"", "no", "false", "0"}:
            return -1
    return level


@dataclass
class Extract:
    roads: gpd.GeoDataFrame
    buildings: gpd.GeoSeries
    skipped: int


class Reader(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.roads = []
        self.buildings = []
        self.skipped = 0
        self.factory = osmium.geom.GeoJSONFactory()

    def way(self, way):
        tags = dict(way.tags)
        if not keep_road(tags):
            return
        coordinates, ids = [], []
        for node in way.nodes:
            if not node.location.valid():
                self.skipped += 1
                return
            coordinates.append((node.lon, node.lat))
            ids.append(node.ref)
        if len(set(coordinates)) < 2:
            return
        self.roads.append({
            "osm_id": way.id,
            "name": tags.get("name:zh", tags.get("name", "")),
            "highway": tags["highway"],
            "junction": tags.get("junction", ""),
            "oneway": tags.get("oneway", ""),
            "ref": tags.get("ref", ""),
            "level": road_level(tags),
            "node_ids": ids,
            "geometry": LineString(coordinates),
        })

    def area(self, area):
        if area.tags.get("building", "no") in {"no", "roof"}:
            return
        import json
        try:
            geometry = shape(json.loads(self.factory.create_multipolygon(area)))
        except (RuntimeError, ValueError):
            return
        if geometry.is_valid and not geometry.is_empty:
            self.buildings.append(geometry)


def read_osm(path: str | Path) -> Extract:
    reader = Reader()
    reader.apply_file(str(path), locations=True)
    if not reader.roads:
        raise ValueError("文件中没有符合筛选条件的道路。请下载包含道路和节点的 OSM PBF 文件。")
    roads = gpd.GeoDataFrame(reader.roads, crs=4326)
    crs = roads.estimate_utm_crs()
    if crs is None:
        raise ValueError("无法确定城市投影，请使用单个城市的下载范围。")
    return Extract(
        roads.to_crs(crs),
        gpd.GeoSeries(reader.buildings, crs=4326).to_crs(crs),
        reader.skipped,
    )


def grade_anchors(roads):
    candidates = {}
    for row in roads[roads.level != 0].itertuples():
        for node_id, xy in zip(row.node_ids, row.geometry.coords):
            entry = candidates.setdefault(node_id, [xy, set()])
            entry[1].add(int(row.level))
    for row in roads[roads.level == 0].itertuples():
        for node_id in row.node_ids:
            if node_id in candidates:
                candidates[node_id][1].add(0)
    return {node_id: value for node_id, value in candidates.items() if len(value[1]) > 1}
