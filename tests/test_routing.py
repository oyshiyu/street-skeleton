import math
import unittest

from shapely.geometry import LineString

from street_skeleton.routing import Network,RouteError


X,Y=761000,2450000


def network(segments,components=None):
    points={};nodes=[];edges=[]
    for cc,level in segments:
        g=LineString([(X+x,Y+y) for x,y in cc]);ids=[]
        for xy in (g.coords[0],g.coords[-1]):
            if xy not in points:points[xy]=len(nodes);nodes.append({'xy':list(xy),'component':0})
            ids.append(points[xy])
        edges.append({'id':len(edges),'u':ids[0],'v':ids[1],'geometry':g,'length_m':g.length,'level':level,'name':'测试通道','wide':False,'component':components[len(edges)] if components else 0})
    return Network(data={'crs':'EPSG:32649','nodes':nodes,'edges':edges})


class RoutingTests(unittest.TestCase):
    def request(self,n,xy,edge=0):return {'point':list(n.to_wgs.transform(X+xy[0],Y+xy[1])),'edge_id':edge}

    def test_partial_edge_and_access_lengths(self):
        n=network([([(0,0),(100,0)],0)])
        r=n.route(self.request(n,(20,10)),self.request(n,(80,-15)))
        self.assertAlmostEqual(r['road_m'],60,places=2)
        self.assertAlmostEqual(r['access_m'],25,places=2)
        self.assertAlmostEqual(r['total_m'],85,places=2)

    def test_curve_is_not_replaced_with_chord(self):
        n=network([([(0,0),(0,100),(100,100)],0)])
        r=n.route(self.request(n,(0,20)),self.request(n,(80,100)))
        self.assertAlmostEqual(r['road_m'],160,places=2)
        self.assertGreater(r['road_m'],math.hypot(80,80)+40)

    def test_loop_uses_shorter_wrapping_arc(self):
        n=network([([(0,0),(100,0),(100,100),(0,100),(0,0)],0)])
        r=n.route(self.request(n,(20,0)),self.request(n,(0,20)))
        self.assertAlmostEqual(r['road_m'],40,places=2)

    def test_geometric_grade_crossing_does_not_create_shortcut(self):
        n=network([([(-100,0),(100,0)],0),([(0,100),(0,-100)],1),([(-100,0),(0,100)],0)])
        a=self.request(n,(50,0),0);b=self.request(n,(0,-50),1)
        r=n.route(a,b)
        self.assertAlmostEqual(r['road_m'],300+math.sqrt(20000),places=2)
        self.assertAlmostEqual(n.route(b,a)['road_m'],r['road_m'],places=3)

    def test_disconnected_network_never_returns_straight_line(self):
        n=network([([(0,0),(100,0)],0),([(0,10),(100,10)],0)],[0,1])
        with self.assertRaises(RouteError) as cm:n.route(self.request(n,(20,0),0),self.request(n,(80,10),1))
        self.assertEqual(cm.exception.code,'disconnected')

    def test_far_point_selects_true_nearest_road(self):
        n=network([([(0,0),(100,0)],0), ([(0,40),(100,40)],1)])
        for offset in (250,1000,5000):
            with self.subTest(offset=offset):
                packet=n.snap(self.request(n,(50,offset))['point'])
                self.assertEqual(packet['candidates'][0]['edge_id'],1)
                self.assertAlmostEqual(packet['candidates'][0]['distance_m'],offset-40,places=2)
                with self.assertRaises(RouteError) as cm:n.snap(packet['point'],0)
                self.assertEqual(cm.exception.code,'access_crosses_road')

    def test_far_access_is_included_in_auto_and_selected_routes(self):
        n=network([([(0,0),(100,0)],0)])
        for edge in (None,0):
            r=n.route(self.request(n,(20,250),edge),self.request(n,(80,-1000),edge))
            self.assertAlmostEqual(r['road_m'],60,places=2)
            self.assertAlmostEqual(r['access_m'],1250,places=2)
            self.assertAlmostEqual(r['total_m'],1310,places=2)

    def test_disconnected_roads_cannot_be_bypassed_by_crossing_nearer_road(self):
        n=network([([(0,0),(100,0)],0), ([(0,40),(100,40)],0)],[0,1])
        a=self.request(n,(20,-20),None);b=self.request(n,(80,60),None)
        with self.assertRaises(RouteError) as cm:n.route(a,b)
        self.assertEqual(cm.exception.code,'disconnected')
        with self.assertRaises(RouteError):n.route(dict(a,edge_id=0),dict(b,edge_id=1))

    def test_joint_optimization_beats_nearest_and_keeps_same_name_candidates(self):
        n=network([([(0,0),(100,0),(100,500),(900,500),(900,0),(1000,0)],0),
                   ([(0,-80),(1000,-80)],0), ([(0,0),(0,-80)],0), ([(1000,0),(1000,-80)],0)])
        a=self.request(n,(50,-20),None);b=self.request(n,(950,-20),None)
        self.assertEqual(n.snap(a['point'])['candidates'][0]['edge_id'],0)
        self.assertIn(1,[c['edge_id'] for c in n.snap(a['point'])['candidates']])
        nearest=n.route(dict(a,edge_id=0),dict(b,edge_id=0));r=n.route(a,b)
        self.assertEqual((r['start']['edge_id'],r['end']['edge_id']),(1,1))
        self.assertAlmostEqual(r['road_m'],900,places=2)
        self.assertAlmostEqual(r['access_m'],120,places=2)
        self.assertAlmostEqual(r['total_m'],1020,places=2)
        self.assertLess(r['total_m'],nearest['total_m'])
        self.assertAlmostEqual(n.route(b,a)['total_m'],r['total_m'],places=3)
        fixed=n.route(dict(a,edge_id=0),b)
        self.assertEqual(fixed['start']['edge_id'],0)
        self.assertEqual(fixed['selection']['start_mode'],'manual')
        brute=min(n.route(dict(a,edge_id=x['edge_id'],position_m=x['position_m']),dict(b,edge_id=y['edge_id'],position_m=y['position_m']))['total_m']
                  for x in n._candidates(a['point'])[0] for y in n._candidates(b['point'])[0])
        self.assertAlmostEqual(r['total_m'],brute,places=3)

    def test_diagonal_endpoint_access_is_rejected(self):
        n=network([([(0,0),(100,0)],0)])
        for xy in [(-10,20),(110,20)]:
            for edge in (None,0):
                with self.subTest(xy=xy,edge=edge),self.assertRaises(RouteError) as cm:
                    n.snap(self.request(n,xy)['point'],edge)
                self.assertEqual(cm.exception.code,'no_perpendicular')

    def test_true_perpendicular_at_endpoint_and_zero_access_are_valid(self):
        n=network([([(0,0),(100,0)],0)])
        for xy,position,distance in [((0,20),0,20),((100,20),100,20),((0,0),0,0)]:
            record=n.snap(self.request(n,xy)['point'],0)
            self.assertAlmostEqual(record['position_m'],position,places=5)
            self.assertAlmostEqual(record['distance_m'],distance,places=3)

    def test_invalid_nearest_corner_is_replaced_by_valid_foot(self):
        n=network([([(0,0),(100,0),(100,100)],0)])
        record=n.snap(self.request(n,(-20,50))['point'])['candidates'][0]
        self.assertAlmostEqual(record['distance_m'],120,places=3)
        self.assertAlmostEqual(record['position_m'],150,places=5)
        self.assertEqual(record['segment_index'],1)

    def test_sloping_road_uses_perpendicular_in_metric_coordinates(self):
        n=network([([(0,0),(100,100)],0)])
        point=self.request(n,(70,30))['point'];record=n.snap(point,0)
        self.assertAlmostEqual(record['position_m'],math.sqrt(5000),places=5)
        self.assertAlmostEqual(record['distance_m'],math.sqrt(800),places=3)
        self.assert_perpendicular(n,point,record)

    def test_enclosed_block_only_offers_its_visible_boundary_roads(self):
        n=network([([(-50,-50),(50,-50)],0), ([(50,-50),(50,50)],0),
                   ([(50,50),(-50,50)],0), ([(-50,50),(-50,-50)],0),
                   ([(-80,-80),(80,-80)],0), ([(80,-80),(80,80)],0),
                   ([(80,80),(-80,80)],0), ([(-80,80),(-80,-80)],0)])
        point=self.request(n,(0,0))['point']
        for radius in (200,800):
            candidates,_=n._candidates(point,radius)
            self.assertEqual({c['edge_id'] for c in candidates},{0,1,2,3})
        for edge in (4,5,6,7):
            with self.assertRaises(RouteError) as cm:n.snap(point,edge)
            self.assertEqual(cm.exception.code,'access_crosses_road')

    def test_same_curved_road_also_blocks_access_to_its_far_side(self):
        n=network([([(0,0),(100,0),(100,100),(0,100),(0,0)],0)])
        point=self.request(n,(-20,50))['point'];candidates=n.snap(point)['candidates']
        self.assertEqual(len(candidates),1)
        self.assertAlmostEqual(candidates[0]['position_m'],350,places=4)
        with self.assertRaises(RouteError) as cm:n.snap(point,0,150)
        self.assertEqual(cm.exception.code,'access_crosses_road')

    def test_point_already_on_network_cannot_use_access_to_jump_roads(self):
        n=network([([(0,0),(100,0)],0), ([(0,40),(100,40)],0), ([(0,0),(0,40)],0)])
        point=self.request(n,(50,0))['point']
        self.assertTrue(all(c['distance_m']==0 for c in n.snap(point)['candidates']))
        with self.assertRaises(RouteError) as cm:n.snap(point,1)
        self.assertEqual(cm.exception.code,'access_crosses_road')

    def test_intermediate_road_vertex_touch_and_overlap_block_access(self):
        for blocker in ([(40,10),(50,0),(60,10)],[(40,0),(60,0)]):
            n=network([([(100,-100),(100,100)],0),(blocker,0)])
            with self.subTest(blocker=blocker),self.assertRaises(RouteError) as cm:
                n.snap(self.request(n,(0,0))['point'],0)
            self.assertEqual(cm.exception.code,'access_crosses_road')

    def test_distinct_feet_on_same_curved_edge_remain_selectable(self):
        n=network([([(0,0),(0,100),(100,100),(100,0)],0)])
        a=self.request(n,(30,50),None);b=self.request(n,(100,20),None)
        packet=n.snap(a['point'])
        self.assertEqual({round(c['position_m']) for c in packet['candidates']},{50,130,250})
        r=n.route(a,b)
        self.assertAlmostEqual(r['total_m'],100,places=2)
        self.assertAlmostEqual(r['start']['position_m'],250,places=4)
        locked=n.route(dict(a,edge_id=0,position_m=50),dict(b,edge_id=0,position_m=280))
        self.assertAlmostEqual(locked['total_m'],260,places=2)
        with self.assertRaises(RouteError):n.route(dict(a,edge_id=0,position_m=0),b)
        for c in packet['candidates']:self.assert_perpendicular(n,a['point'],c)

    def assert_perpendicular(self,n,point,record):
        self.assertEqual(record['projection'],'perpendicular')
        p=n.point(point);q=n.point(record['point']);g=n.geometry[record['edge_id']]
        a,b=list(g.coords)[record['segment_index']:record['segment_index']+2]
        dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
        self.assertLess(abs(((p.x-q.x)*dx+(p.y-q.y)*dy)/length),1e-5)
        along=((q.x-a[0])*dx+(q.y-a[1])*dy)/length
        self.assertGreaterEqual(along,-1e-5);self.assertLessEqual(along,length+1e-5)

    def test_search_expands_only_when_local_candidates_are_disconnected(self):
        n=network([([(0,0),(50,0)],0), ([(2000,0),(2050,0)],0),
                   ([(0,300),(2000,300)],0)],[0,1,2])
        r=n.route(self.request(n,(0,10),None),self.request(n,(2000,10),None))
        self.assertTrue(r['selection']['expanded'])
        self.assertEqual(r['selection']['radius_m'],{'start':400,'end':400})
        self.assertAlmostEqual(r['total_m'],2580,places=2)

    def test_disconnected_auto_does_not_make_unlimited_access_shortcut(self):
        n=network([([(0,0),(100,0)],0), ([(5000,0),(5100,0)],0)],[0,1])
        with self.assertRaises(RouteError) as cm:
            n.route(self.request(n,(20,0),None),self.request(n,(5080,0),None))
        self.assertEqual(cm.exception.code,'disconnected')

    def test_nan_is_rejected(self):
        n=network([([(0,0),(100,0)],0)])
        with self.assertRaises(RouteError):n.snap([float('nan'),22])

    def test_same_location_has_zero_network_distance(self):
        n=network([([(0,0),(100,0)],0)])
        point=self.request(n,(25,0));r=n.route(point,point)
        self.assertAlmostEqual(r['road_m'],0,places=3)



if __name__=='__main__':unittest.main()
