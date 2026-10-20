import math

import geopandas as gpd
import networkx as nx
import shapely
from shapely.geometry import LineString, Point
from shapely.ops import substring


PRECISION = 0.001


def lines(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "LineString":
        return [geometry] if geometry.length > PRECISION else []
    return [line for part in getattr(geometry, "geoms", []) for line in lines(part)]


def coordinate(xy):
    return round(xy[0], 3), round(xy[1], 3)


def node_lines(geometries, anchors=()):
    parts = lines(shapely.union_all(geometries, grid_size=PRECISION))
    points = [Point(coordinate(p.coords[0])) for p in anchors]
    tree = shapely.STRtree(points)
    result = []
    for line in parts:
        cuts = [(0, None), (line.length, None)]
        for index in tree.query(line.buffer(0.002)):
            point = points[int(index)]
            if line.distance(point) <= 0.002:
                cuts.append((line.project(point), point))
        ordered = []
        for distance, point in sorted(cuts, key=lambda item: item[0]):
            if ordered and distance - ordered[-1][0] < PRECISION:
                if point is not None:
                    ordered[-1] = distance, point
            else:
                ordered.append((distance, point))
        for (start, pa), (end, pb) in zip(ordered, ordered[1:]):
            part = substring(line, start, end)
            if part.geom_type != "LineString" or part.length <= PRECISION:
                continue
            coords = list(part.coords)
            coords[0] = coordinate(pa.coords[0] if pa is not None else coords[0])
            coords[-1] = coordinate(pb.coords[0] if pb is not None else coords[-1])
            result.append(LineString(coords))
    return result


def build_graph(strata, anchors, crs):
    shared = {}
    for node_id, (xy, levels) in anchors.items():
        for level in levels:
            shared[(coordinate(xy), level)] = str(node_id)

    def key(xy, level):
        xy = coordinate(xy)
        return (*xy, "osm:" + shared[(xy, level)] if (xy, level) in shared else str(level))

    graph = nx.MultiGraph()
    for level, geometries in strata.items():
        for geometry in geometries:
            graph.add_edge(key(geometry.coords[0], level), key(geometry.coords[-1], level),
                           geometry=geometry, levels={level})

    visited, merged = set(), []

    def token(a, b, edge_key):
        return frozenset((a, b)), edge_key

    def stop(node):
        return graph.degree(node) != 2 or len({level for _, _, data in graph.edges(node, data=True)
                                              for level in data['levels']}) > 1

    starts = [node for node in graph if stop(node)]
    for component in nx.connected_components(graph):
        if not any(stop(node) for node in component):
            starts.append(min(component))
    for start in starts:
        for _, current, edge_key, data in graph.edges(start, keys=True, data=True):
            edge = token(start, current, edge_key)
            if edge in visited:
                continue
            visited.add(edge)
            coords = list(data["geometry"].coords)
            if math.dist(coords[0], start[:2]) > math.dist(coords[-1], start[:2]):
                coords.reverse()
            levels = set(data["levels"])
            while not stop(current) and current != start:
                options = [(v, k, d) for _, v, k, d in graph.edges(current, keys=True, data=True)
                           if token(current, v, k) not in visited]
                if not options:
                    break
                following, k, d = options[0]
                visited.add(token(current, following, k))
                extra = list(d["geometry"].coords)
                if math.dist(extra[0], current[:2]) > math.dist(extra[-1], current[:2]):
                    extra.reverse()
                coords.extend(extra[1:])
                levels.update(d["levels"])
                current = following
            coords[0], coords[-1] = start[:2], current[:2]
            merged.append((start, current, LineString(coords), levels))
    if len(visited) != graph.number_of_edges():
        raise RuntimeError("路网合并时出现未遍历的道路。")
    final = nx.MultiGraph()
    for i, (a, b, _, _) in enumerate(merged):
        final.add_edge(a, b, key=i)
    order = sorted(final, key=lambda node: (node[1], node[0], node[2]))
    ids = {node: i + 1 for i, node in enumerate(order)}
    components = sorted(nx.connected_components(final), key=len, reverse=True)
    component_ids = {node: i + 1 for i, group in enumerate(components) for node in group}
    nodes = gpd.GeoDataFrame([{
        "node_id": ids[node], "degree": final.degree(node),
        "kind": "intersection" if final.degree(node) >= 3 else (
            "dead_end" if final.degree(node) == 1 else "loop_anchor"),
        "component": component_ids[node], "geometry": Point(node[:2]),
    } for node in order], crs=crs)
    roads = gpd.GeoDataFrame([{
        "edge_id": i + 1, "from_node": ids[a], "to_node": ids[b],
        "length_m": geometry.length, "levels": ",".join(map(str, sorted(levels))),
        "component": component_ids[a], "geometry": geometry,
    } for i, (a, b, geometry, levels) in enumerate(merged)], crs=crs)
    return roads, nodes, len(components)


def validate(roads, nodes):
    if roads.empty or nodes.empty:
        raise ValueError("未生成有效路网。")
    if not roads.is_valid.all() or not nodes.is_valid.all():
        raise ValueError("路网包含无效几何。")
    if not roads.edge_id.is_unique or not nodes.node_id.is_unique:
        raise ValueError("路网编号重复。")
    positions = nodes.set_index("node_id").geometry
    degrees = dict.fromkeys(positions.index, 0)
    for row in roads.itertuples():
        if Point(row.geometry.coords[0]).distance(positions[row.from_node]) > 1e-8 or Point(row.geometry.coords[-1]).distance(positions[row.to_node]) > 1e-8:
            raise ValueError("道路端点与节点不一致。")
        degrees[row.from_node] += 1
        degrees[row.to_node] += 1
    if any(degrees[row.node_id] != row.degree for row in nodes.itertuples()):
        raise ValueError("交叉点连接数不一致。")
