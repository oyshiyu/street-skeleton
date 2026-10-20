import collections
import gzip
import json
import math
from pathlib import Path

import shapely
from pyproj import CRS, Transformer
from shapely.geometry import LineString

from .routing import Network


def network_data(result):
    crs = CRS(result.roads.crs)
    if not crs.is_projected or any(abs(axis.unit_conversion_factor - 1) > 1e-9 for axis in crs.axis_info):
        raise ValueError('选线路网需要以米为单位的本地投影。')
    rows = list(result.nodes.itertuples())
    ids = {int(row.node_id): i for i, row in enumerate(rows)}
    nodes = [{'xy': list(row.geometry.coords[0]), 'component': int(row.component)} for row in rows]
    sources = {}
    for level, group in result.source.groupby('level'):
        sources[int(level)] = (shapely.STRtree(list(group.geometry)), list(group['name']))
    edges = []
    for row in result.roads.itertuples():
        levels = [int(v) for v in str(row.levels).split(',')]
        if len(levels) != 1:
            raise ValueError('路段包含多个层级，请重新提取路网以保留桥隧分层节点。')
        level = levels[0]
        name = ''
        if level in sources:
            tree, names = sources[level]
            nearest = tree.nearest(row.geometry.interpolate(.5, normalized=True))
            if nearest is not None:
                name = names[int(nearest)]
        edges.append({'id': len(edges), 'u': ids[int(row.from_node)], 'v': ids[int(row.to_node)],
                      'geometry': [list(xy) for xy in row.geometry.coords],
                      'length_m': float(row.geometry.length), 'level': level, 'name': name,
                      'wide': False, 'component': int(row.component)})
    checks = {**result.stats, 'analysis_crs': crs.to_string(), 'edges': len(edges), 'nodes': len(nodes)}
    return {'crs': crs.to_string(), 'nodes': nodes, 'edges': edges, 'checks': checks}


def write_network(result, path):
    Path(path).write_text(json.dumps(network_data(result), ensure_ascii=False, separators=(',', ':')), encoding='utf-8')


def read_network(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    for edge in data['edges']:
        edge['geometry'] = LineString(edge['geometry'])
    return Network(data=data)


def planner_payload(network):
    transformer = Transformer.from_crs(network.crs, 3857, always_xy=True)
    projected = [[transformer.transform(*xy) for xy in g.coords] for g in network.geometry]
    points = [xy for line in projected for xy in line]
    origin = [min(p[0] for p in points), min(p[1] for p in points)]
    bounds = [0, 0, max(p[0] for p in points) - origin[0], max(p[1] for p in points) - origin[1]]
    roads = [{'id': edge['id'], 'points': [[round(x - origin[0], 2), round(y - origin[1], 2)] for x, y in line]}
             for edge, line in zip(network.edges, projected)]
    counts = collections.Counter(n['component'] for n in network.nodes)
    largest = counts.most_common(1)[0][0]
    candidates = [n['xy'] for n in network.nodes if n['component'] == largest]
    a = min(candidates)
    b = max(candidates, key=lambda p: math.dist(a, p))
    if a == b:
        edge = max(network.edges, key=lambda e: e['length_m'])
        a = edge['geometry'].interpolate(.2, normalized=True).coords[0]
        b = edge['geometry'].interpolate(.8, normalized=True).coords[0]
    return {'origin': origin, 'bounds': bounds, 'roads': roads, 'stats': network.checks,
            'crs': 'EPSG:3857', 'analysis_crs': network.crs,
            'demo_points': [list(network.to_wgs.transform(*p)) for p in (a, b)],
            'attribution': '© OpenStreetMap contributors',
            'tile_url': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'}


def load_planner(directory):
    network = read_network(Path(directory) / 'cable-network.json')
    data = gzip.compress(json.dumps(planner_payload(network), ensure_ascii=False, separators=(',', ':')).encode())
    return network, data
