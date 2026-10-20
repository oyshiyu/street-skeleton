import heapq
import math

import shapely
from pyproj import Transformer
from shapely.geometry import LineString,Point
from shapely.ops import substring


class RouteError(ValueError):
    def __init__(self,message,code='invalid_request'):
        super().__init__(message);self.code=code


class Network:
    def __init__(self,data):
        self.nodes=data['nodes'];self.edges=data['edges'];self.checks=data.get('checks',{})
        self.geometry=[e['geometry'] for e in self.edges]
        self.tree=shapely.STRtree(self.geometry)
        self.bounds=shapely.total_bounds(self.geometry) if self.geometry else None
        self.adj=[[] for _ in self.nodes]
        for e in self.edges:
            self.adj[e['u']].append((e['v'],e['id'],True))
            self.adj[e['v']].append((e['u'],e['id'],False))
        self.crs=data['crs']
        self.to_metric=Transformer.from_crs(4326,self.crs,always_xy=True)
        self.to_wgs=Transformer.from_crs(self.crs,4326,always_xy=True)

    def point(self,lonlat):
        if not isinstance(lonlat,(list,tuple)) or len(lonlat)!=2:raise RouteError('请选择地图上的一个位置。')
        try:lon,lat=map(float,lonlat)
        except (ValueError,TypeError):raise RouteError('选点坐标无效。')
        if not(math.isfinite(lon) and math.isfinite(lat) and -180<=lon<=180 and -85<=lat<=85):raise RouteError('选点坐标无效。')
        xy=self.to_metric.transform(lon,lat)
        if not all(math.isfinite(v) for v in xy):raise RouteError('选点坐标无法转换到当前地图。')
        return Point(xy)

    def _foot_records(self,p,i):
        g=self.geometry[i];coords=list(g.coords);records=[];position=0;seen=set()
        for segment,(a,b) in enumerate(zip(coords,coords[1:])):
            dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
            if length==0:continue
            along=((p.x-a[0])*dx+(p.y-a[1])*dy)/length
            if -1e-7<=along<=length+1e-7:
                along=max(0,min(length,along));t=position+along;key=round(t,6)
                if key not in seen:
                    seen.add(key);records.append(self._snap_record(p,i,t,segment))
            position+=length
        records.sort(key=lambda c:(c['distance_m'],c['position_m']))
        return records

    def _local_access(self,p,record):
        q=self.geometry[record['edge_id']].interpolate(record['position_m'])
        length=p.distance(q)
        if length<=1e-6:return True
        connector=LineString([p,q])
        prefix=substring(connector,0,max(0,length-2e-6))
        for index in self.tree.query(prefix.buffer(1e-6).envelope):
            if prefix.distance(self.geometry[int(index)])<=1e-6:return False
        return True

    def _candidates(self,lonlat,min_radius=200):
        p=self.point(lonlat);nearest=self.tree.nearest(p)
        if nearest is None:raise RouteError('当前地图没有可接入的道路通道。','empty_network')
        radius=max(min_radius,p.distance(self.geometry[int(nearest)])+50);cache={};has_feet=False
        def within(limit):
            nonlocal has_feet
            result=[]
            for index in self.tree.query(p.buffer(limit).envelope):
                i=int(index)
                if i not in cache:
                    feet=self._foot_records(p,i);has_feet=has_feet or bool(feet)
                    cache[i]=[c for c in feet if self._local_access(p,c)]
                result.extend(c for c in cache[i] if c['distance_m']<=limit)
            return sorted(result,key=lambda c:(c['distance_m'],c['edge_id'],c['position_m']))
        x0,y0,x1,y1=self.bounds
        cover=max(math.hypot(p.x-x,p.y-y) for x,y in ((x0,y0),(x0,y1),(x1,y0),(x1,y1)))+1
        while True:
            candidates=within(radius)
            if candidates:
                actual=max(min_radius,candidates[0]['distance_m']+50)
                return (within(actual) if actual!=radius else candidates),actual
            if radius>=cover:
                if not has_feet:raise RouteError('该位置没有垂足落在实际道路上的接入方案，请更换选点。','no_perpendicular')
                raise RouteError('该位置没有不跨越其他道路的有效垂足，请更换选点。','no_local_access')
            radius=min(cover,radius*2)

    def snap(self,lonlat,edge_id=None,position_m=None):
        if edge_id is not None:
            p=self.point(lonlat)
            if type(edge_id) is not int or not 0<=edge_id<len(self.edges):raise RouteError('通道编号无效。')
            records=self._foot_records(p,edge_id)
            if not records:raise RouteError('该点无法垂直接入所选道路，垂足位于道路范围外。','no_perpendicular')
            if position_m is not None:
                if type(position_m) not in (int,float) or not math.isfinite(position_m):raise RouteError('接入位置无效。')
                for record in records:
                    if abs(record['position_m']-position_m)<1e-5:
                        if not self._local_access(p,record):raise RouteError('接入线会先经过其他道路，请选择所在街区周边的道路。','access_crosses_road')
                        return record
                raise RouteError('所选位置不是该点在道路上的有效垂足。','no_perpendicular')
            for record in records:
                if self._local_access(p,record):return record
            raise RouteError('接入线会先经过其他道路，请选择所在街区周边的道路。','access_crosses_road')
        candidates,radius=self._candidates(lonlat)
        result=candidates[:12]
        return {'point':list(map(float,lonlat)),'candidates':result,'radius_m':round(radius,3),
                'ambiguous_grade':len({c['level'] for c in result if c['distance_m']<=result[0]['distance_m']+8})>1}

    def _snap_record(self,p,i,t,segment):
        e=self.edges[i];q=self.geometry[i].interpolate(t)
        return {'edge_id':i,'name':e['name'] or '未命名道路','level':e['level'],'distance_m':round(p.distance(q),3),
                'point':list(self.to_wgs.transform(q.x,q.y)),'component':e['component'],'position_m':t,
                'segment_index':segment,'projection':'perpendicular','access_boundary':'first_road'}

    def route(self,start,end):
        if not isinstance(start,dict) or not isinstance(end,dict):raise RouteError('请先选择起点和终点。')
        items=(start,end);modes=['manual' if item.get('edge_id') is not None else 'auto' for item in items]
        for radius in (200,400,800):
            choices=[];radii=[]
            for item,mode in zip(items,modes):
                if mode=='manual':
                    choices.append([self.snap(item.get('point'),item['edge_id'],item.get('position_m'))]);radii.append(None)
                else:
                    candidates,actual=self._candidates(item.get('point'),radius)
                    choices.append(candidates);radii.append(round(actual,3))
            common={s['component'] for s in choices[0]} & {s['component'] for s in choices[1]}
            if common:
                choices=[[s for s in group if s['component'] in common] for group in choices]
                result=self._search(choices)
                if result is not None:
                    result['selection']={'start_mode':modes[0],'end_mode':modes[1],
                        'radius_m':{'start':radii[0],'end':radii[1]},
                        'candidate_count':{'start':len(choices[0]),'end':len(choices[1])},
                        'expanded':radius>200,'objective':'total_length','access_rule':'perpendicular_first_road'}
                    return result
            if all(mode=='manual' for mode in modes):break
        raise RouteError('候选接入通道之间不连通，请更换选点或手动指定其他通道。','disconnected')

    def _search(self,choices):
        extra={};on_edge={};virtual=-3
        def directed(u,v,length,step):
            extra.setdefault(u,[]).append((v,max(0,float(length)),step))
        def road(u,v,g,t0,t1,eid):
            cc=list(substring(g,t0,t1).coords)
            directed(u,v,abs(t1-t0),('partial',cc,eid))
            directed(v,u,abs(t1-t0),('partial',cc[::-1],eid))
        for role,group in enumerate(choices):
            for index,snap in enumerate(group):
                node=virtual;virtual-=1;e=self.edges[snap['edge_id']];g=e['geometry'];t=snap['position_m']
                if role==0:directed(-1,node,snap['distance_m'],('access',role,index))
                else:directed(node,-2,snap['distance_m'],('access',role,index))
                road(node,e['u'],g,t,0,e['id']);road(node,e['v'],g,t,g.length,e['id'])
                on_edge.setdefault(e['id'],[]).append((t,node))
        for eid,positions in on_edge.items():
            positions.sort()
            for (ta,na),(tb,nb) in zip(positions,positions[1:]):road(na,nb,self.geometry[eid],ta,tb,eid)
        distance={-1:0};parents={};heap=[(0,-1)];done=set()
        while heap:
            cost,u=heapq.heappop(heap)
            if u in done:continue
            done.add(u)
            if u==-2:break
            options=[]
            if u>=0:
                options.extend((v,self.edges[eid]['length_m'],('edge',eid,forward)) for v,eid,forward in self.adj[u])
            options.extend(extra.get(u,()))
            for v,weight,step in options:
                new=cost+weight
                if new<distance.get(v,math.inf)-1e-9:
                    distance[v]=new;parents[v]=(u,step);heapq.heappush(heap,(new,v))
        if -2 not in done:return None
        steps=[];v=-2
        while v!=-1:
            u,step=parents[v];steps.append(step);v=u
        steps.reverse();coords=[];edge_ids=[];snaps={}
        for step in steps:
            if step[0]=='access':
                _,role,index=step;snaps[role]=choices[role][index];continue
            if step[0]=='edge':
                _,eid,forward=step;cc=list(self.geometry[eid].coords)
                if not forward:cc.reverse()
            else:_,cc,eid=step
            for xy in cc:
                if not coords or math.dist(coords[-1],xy)>1e-7:coords.append(xy)
            edge_ids.append(eid)
        a,b=snaps[0],snaps[1]
        measured=sum(math.dist(x,y) for x,y in zip(coords,coords[1:]));access=a['distance_m']+b['distance_m']
        assert abs(measured+access-distance[-2])<.01,(measured,access,distance[-2])
        warnings=[]
        if any(self.edges[i]['wide'] for i in edge_ids):warnings.append('途经宽间距道路，按一个空间通道估算。')
        if any(self.edges[i]['level']!=0 for i in edge_ids):warnings.append('途经桥隧通道，敷设条件需确认。')
        return {'road_m':round(measured,3),'access_m':round(access,3),'total_m':round(measured+access,3),
                'start':a,'end':b,'points':[list(self.to_wgs.transform(*p)) for p in coords],
                'edge_ids':list(dict.fromkeys(edge_ids)),'warnings':warnings,
                'source_snapshot':self.checks.get('source_snapshot'),
                'method':'垂直接入所在区域可见的首条道路，接入段不得先碰到其他道路；候选内联合优化总长度；未核查建筑水体；长度使用城市本地米制投影折线'}
