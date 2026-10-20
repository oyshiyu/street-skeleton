import gzip
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest

import geopandas as gpd
from shapely.geometry import LineString

from street_skeleton.cable import read_network, write_network
from street_skeleton.engine import Result, run
from street_skeleton.server import Server
from street_skeleton.topology import build_graph


class CableTests(unittest.TestCase):
    def test_grade_transition_and_city_crs_survive_export(self):
        x, y = 334000, 6252000
        a, b, c = (x, y), (x + 100, y), (x + 200, y)
        roads, nodes, _ = build_graph({0: [LineString([a, b])], 1: [LineString([b, c])]},
                                      {10: (b, {0, 1})}, 32756)
        self.assertEqual(len(roads), 2)
        source = gpd.GeoDataFrame({'name': ['地面路', '桥'], 'level': [0, 1]},
                                 geometry=[LineString([a, b]), LineString([b, c])], crs=32756)
        result = Result(roads, nodes, source, gpd.GeoDataFrame(geometry=[], crs=32756), {})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cable-network.json'
            write_network(result, path)
            network = read_network(path)
            self.assertEqual(network.to_metric.target_crs.to_epsg(), 32756)
            self.assertEqual({e['level'] for e in network.edges}, {0, 1})
            start = list(network.to_wgs.transform(x + 20, y))
            end = list(network.to_wgs.transform(x + 180, y))
            route = network.route({'point': start}, {'point': end})
            self.assertAlmostEqual(route['total_m'], 160, places=2)

    def test_city_file_to_saved_network_to_http_route(self):
        xml = '''<osm version="0.6">
        <node id="1" lat="-33.86" lon="151.20"/>
        <node id="2" lat="-33.86" lon="151.202"/>
        <node id="3" lat="-33.86" lon="151.204"/>
        <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/>
        <tag k="highway" v="pedestrian"/><tag k="name" v="测试步行街"/></way></osm>'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'sample.osm'
            path.write_text(xml)
            run(path, root / 'results', progress=lambda _: None)
            server = Server(0, root, root / 'results')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                def request(method, path, data=None, headers=None):
                    connection.request(method, path, body=None if data is None else json.dumps(data), headers=headers or {})
                    response = connection.getresponse()
                    return response.status, response.read()
                status, html = request('GET', '/planner/')
                self.assertEqual(status, 200)
                self.assertIn(server.token.encode(), html)
                headers = {'X-Cable-Token': server.token, 'Content-Type': 'application/json'}
                self.assertEqual(request('GET', '/planner/data.json.gz')[0], 403)
                status, body = request('GET', '/planner/data.json.gz', headers=headers)
                self.assertEqual(status, 200)
                payload = json.loads(gzip.decompress(body))
                self.assertEqual(payload['analysis_crs'], 'EPSG:32756')
                self.assertEqual(payload['stats']['city_name'], 'sample')
                a, b = payload['demo_points']
                status, body = request('POST', '/planner/api/route', {'start': {'point': a}, 'end': {'point': b}}, headers)
                self.assertEqual(status, 200)
                route = json.loads(body)
                self.assertGreater(route['total_m'], 300)
                self.assertEqual(route['selection']['access_rule'], 'perpendicular_first_road')
                self.assertEqual(request('POST', '/planner/api/snap', {'point': a},
                                         {**headers, 'Origin': 'https://other.example'})[0], 403)
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
