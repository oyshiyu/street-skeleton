import json
from pathlib import Path
import tempfile
import unittest

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

from street_skeleton.engine import export, process
from street_skeleton.osm import Extract, keep_road, read_osm, road_level
from street_skeleton.topology import build_graph, node_lines, validate


def extract(rows, buildings=()):
    records = []
    for index, row in enumerate(rows):
        coords = row.pop("coords")
        records.append({
            "osm_id": index + 1, "name": "", "highway": "residential", "junction": "",
            "oneway": "", "ref": "", "level": 0,
            "node_ids": list(range(index * 100 + 1, index * 100 + 1 + len(coords))),
            "geometry": LineString(coords), **row,
        })
    return Extract(gpd.GeoDataFrame(records, crs=32649), gpd.GeoSeries(buildings, crs=32649), 0)


class NetworkTests(unittest.TestCase):
    def test_streets_and_alleys(self):
        for tags in [dict(highway="pedestrian"), dict(highway="residential"),
                     dict(highway="footway", name="巷道"), dict(highway="service", service="alley")]:
            self.assertTrue(keep_road(tags))
        for tags in [dict(highway="steps"), dict(highway="path"), dict(highway="footway"),
                     dict(highway="footway", name="某路", footway="sidewalk"),
                     dict(highway="pedestrian", indoor="yes"), dict(highway="service", service="driveway")]:
            self.assertFalse(keep_road(tags))
        self.assertEqual(road_level({"bridge":"yes"}), 1)
        self.assertEqual(road_level({"tunnel":"building_passage"}), -1)

    def test_crossing_is_one_junction(self):
        source = extract([
            dict(coords=[(-100,0),(0,0),(100,0)], node_ids=[1,2,3]),
            dict(coords=[(0,-100),(0,0),(0,100)], node_ids=[4,2,5]),
        ])
        result = process(source)
        junctions = result.nodes[result.nodes.degree >= 3]
        self.assertEqual(len(junctions), 1)
        self.assertEqual(junctions.iloc[0].degree, 4)
        self.assertEqual(len(result.roads), 4)
        validate(result.roads, result.nodes)

    def test_overpass_does_not_connect(self):
        source = extract([
            dict(coords=[(-100,0),(100,0)], node_ids=[1,2]),
            dict(coords=[(0,-100),(0,100)], node_ids=[3,4], level=1),
        ])
        result = process(source)
        self.assertEqual(result.stats["components"], 2)
        self.assertEqual(result.stats["intersections"], 0)

    def test_true_grade_transition_connects(self):
        source = extract([
            dict(coords=[(-100,0),(0,0)], node_ids=[1,2]),
            dict(coords=[(0,0),(100,0)], node_ids=[2,3], level=1),
            dict(coords=[(0,0),(0,100)], node_ids=[2,4]),
        ])
        result = process(source)
        self.assertEqual(result.stats["components"], 1)
        self.assertEqual(result.stats["review_points"], 0)
        self.assertEqual(result.stats["intersections"], 1)

    def test_parallel_open_bridge_is_one_centerline(self):
        source = extract([
            dict(coords=[(0,-10),(300,-10)], name="大桥", oneway="yes", level=1),
            dict(coords=[(300,10),(0,10)], name="大桥", oneway="yes", level=1),
        ])
        result = process(source)
        self.assertEqual(len(result.roads), 1)
        self.assertAlmostEqual(result.roads.iloc[0].geometry.length, 300, places=2)
        self.assertLess(result.roads.iloc[0].geometry.distance(Point(150,0)), .001)

    def test_building_block_is_preserved(self):
        source = extract([
            dict(coords=[(0,0),(160,0),(160,160),(0,160),(0,0)]),
            dict(coords=[(-100,0),(0,0)]),
        ], [Polygon([(25,25),(135,25),(135,135),(25,135)])])
        result = process(source)
        building = source.buildings.iloc[0]
        self.assertTrue(all(not geometry.intersects(building) for geometry in result.roads.geometry))
        self.assertTrue(any(geometry.length > 500 for geometry in result.roads.geometry))

    def test_bends_and_closed_cycles(self):
        ring = LineString([(0,0),(100,0),(100,100),(0,100),(0,0)])
        roads,nodes,_ = build_graph({0:node_lines([ring])}, {}, 32649)
        self.assertEqual(len(roads),1)
        self.assertEqual(len(nodes),1)
        self.assertTrue(roads.iloc[0].geometry.is_ring)
        validate(roads,nodes)

    def test_tagged_roundabout_is_one_junction(self):
        source=extract([
            dict(coords=[(-20,-20),(20,-20),(20,20),(-20,20),(-20,-20)],junction="roundabout"),
            dict(coords=[(-100,-20),(-20,-20)]),
            dict(coords=[(20,-20),(100,-20)]),
            dict(coords=[(20,20),(100,20)]),
            dict(coords=[(-20,20),(-100,20)]),
        ])
        result=process(source)
        self.assertEqual(result.stats["intersections"],1)
        self.assertEqual(len(result.roads),4)

    def test_reader_projects_another_city_and_export(self):
        xml = '''<osm version="0.6">
        <node id="1" lat="-33.86" lon="151.20"/>
        <node id="2" lat="-33.86" lon="151.202"/>
        <way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="pedestrian"/></way>
        </osm>'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"city.osm"
            path.write_text(xml)
            source=read_osm(path)
            self.assertEqual(source.roads.crs.to_epsg(),32756)
            result=process(source)
            output=Path(directory)/"results"
            export(result,output)
            self.assertEqual(gpd.read_file(output/"street-skeleton.gpkg",layer="roads").shape[0],1)
            self.assertEqual(json.loads((output/"intersections.geojson").read_text())["features"],[])
            html=(output/"map.html").read_text()
            self.assertIn("window.PRELOADED=",html)
            self.assertNotIn('src="/app.js"',html)
            self.assertNotIn('href="/style.css"',html)
            with self.assertRaises(FileExistsError):
                export(result,output)


if __name__ == "__main__":
    unittest.main()
