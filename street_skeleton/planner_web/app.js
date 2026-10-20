'use strict';
const $=id=>document.getElementById(id),canvas=$('map'),context=canvas.getContext('2d');
const TOKEN=document.querySelector('meta[name="cable-token"]').content;
const R=6378137,WORLD=2*Math.PI*R,HALF=WORLD/2;
const state={data:null,center:null,scale:1,start:null,end:null,route:null,pick:'start',busy:false,revision:0,drag:null};
const tiles=new Map();let queued=false,tileTimer=null,tilesAfter=0;

function local(lonlat){const [lon,lat]=lonlat;return [lon*Math.PI/180*R-state.data.origin[0],Math.log(Math.tan(Math.PI/4+lat*Math.PI/360))*R-state.data.origin[1]];}
function lonlat(p){const x=p[0]+state.data.origin[0],y=p[1]+state.data.origin[1];return [x/R*180/Math.PI,(2*Math.atan(Math.exp(y/R))-Math.PI/2)*180/Math.PI];}
function screen(p){return [(p[0]-state.center[0])*state.scale+canvas.clientWidth/2,(state.center[1]-p[1])*state.scale+canvas.clientHeight/2];}
function mapPoint(x,y){return [state.center[0]+(x-canvas.clientWidth/2)/state.scale,state.center[1]-(y-canvas.clientHeight/2)/state.scale];}
function length(m){return m>=1000?(m/1000).toFixed(2)+' km':Math.round(m).toLocaleString('zh-CN')+' m';}
function grade(level){return level===0?'地面通道':level>0?'上跨通道 '+level:'下穿通道 '+level;}

async function api(path,data){
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Cable-Token':TOKEN},body:JSON.stringify(data)});
  const result=await response.json();if(!response.ok)throw Object.assign(new Error(result.error||'本次计算未完成。'),{code:result.code});return result;
}
function status(message,error=false){$('status').textContent=message;$('status').className=(error?'error':'')+(state.busy?' busy':'');}
function setBusy(value){state.busy=value;for(const id of ['demo','swap','repick-start','repick-end','start-channel','end-channel'])$(id).disabled=value;}
function candidateKey(c){return c.edge_id+':'+c.position_m.toFixed(6);}
function selected(point){return point.choice_id==='auto'?point.chosen:point.packet.candidates.find(c=>candidateKey(c)===point.choice_id);}
function endpointRequest(point){const c=selected(point);return {point:point.packet.point,...(point.choice_id==='auto'?{}:{edge_id:c.edge_id,position_m:c.position_m})};}
function refresh(){
  for(const role of ['start','end']){
    const point=state[role],label=$(role+'-label'),select=$(role+'-channel'),hint=$(role+'-hint');
    $(role+'-card').classList.toggle('active',state.pick===role||state.pick==='new'&&role==='start');
    select.replaceChildren();select.hidden=!point;
    if(point){
      label.textContent=point.packet.point.map(v=>v.toFixed(5)).join(', ');
      const other=state[role==='start'?'end':'start'],otherChoice=other?selected(other):null;
      const automatic=document.createElement('option');automatic.value='auto';automatic.textContent='自动 · 周边道路内比较总长';automatic.selected=point.choice_id==='auto';select.append(automatic);
      for(const option of point.packet.candidates){const o=document.createElement('option');o.value=candidateKey(option);o.textContent=grade(option.level)+' · '+option.name+' · '+Math.round(option.distance_m)+'m'+(otherChoice&&option.component===otherChoice.component?' · 可连通':'');o.selected=candidateKey(option)===point.choice_id;select.append(o);}
      const choice=selected(point),notes=[];
      if(point.choice_id==='auto')notes.push((state.route?'已选：':'暂接：')+choice.name+' · '+length(choice.distance_m)+'。');
      if(choice.distance_m>200)notes.push('接入距离 '+length(choice.distance_m)+'，按垂直距离估算。');
      if(point.packet.ambiguous_grade)notes.push('附近有不同层通道，可在上方切换接入位置。');
      hint.textContent=notes.join('');
    }else{label.textContent=role==='start'?'点击地图选择起点':'接着点击地图选择终点';hint.textContent='';}
  }
  $('result').hidden=!state.route;$('swap').disabled=state.busy||!state.start||!state.end;
  $('instruction').textContent=state.busy?'正在计算…':state.route?'再点两点可开始新路线':state.pick==='end'?'点击地图选择终点':'点击地图选择起点';
  if(state.route){
    const r=state.route,total=r.total_m>=1000?[(r.total_m/1000).toFixed(2),'km']:[Math.round(r.total_m).toLocaleString('zh-CN'),'m'];
    $('total').replaceChildren(document.createTextNode(total[0]));const unit=document.createElement('small');unit.textContent=total[1];$('total').append(unit);
    $('road-length').textContent=length(r.road_m);$('start-access').textContent=length(r.start.distance_m);$('end-access').textContent=length(r.end.distance_m);
    const notes=[...r.warnings];if(r.selection.expanded)notes.unshift('附近未找到连通方案，已扩大接入搜索范围。');
    $('route-notes').replaceChildren();for(const note of notes){const p=document.createElement('p');p.textContent=note;$('route-notes').append(p);}
  }
  schedule();
}

async function calculate(fit=false){
  if(!state.start||!state.end)return;
  const revision=++state.revision;setBusy(true);state.route=null;status('正在联合比较接入位置与沿路总长度…');refresh();
  try{
    const result=await api('/planner/api/route',{start:endpointRequest(state.start),end:endpointRequest(state.end)});
    if(revision!==state.revision)return;
    for(const role of ['start','end']){const point=state[role];point.chosen=result[role];if(!point.packet.candidates.some(c=>candidateKey(c)===candidateKey(result[role])))point.packet.candidates.push(result[role]);}
    result.path=result.points.map(local);state.route=result;state.pick='new';setBusy(false);status('路线已生成 · 可手动指定接入通道');refresh();if(fit)fitRoute();
  }catch(error){if(revision!==state.revision)return;setBusy(false);state.pick='new';status(error.message+(error.code==='disconnected'?' 可在下方切换标有“可连通”的候选通道。':''),true);refresh();$('instruction').textContent=error.code==='disconnected'?'当前通道不连通，可更换选点或接入通道':'请更换选点或接入通道';}
}

async function choose(point){
  if(!state.data||state.busy)return;
  const revision=++state.revision,target=state.pick;setBusy(true);status('正在寻找候选接入道路…');refresh();
  try{
    const packet=await api('/planner/api/snap',{point});if(revision!==state.revision)return;
    if(target==='new'){state.start=null;state.end=null;state.route=null;}
    const role=target==='new'?'start':target;
    state[role]={packet,choice_id:'auto',chosen:packet.candidates[0]};state.route=null;setBusy(false);
    state.pick=role==='start'&&!state.end?'end':!state.start?'start':'new';
    status(state.start&&state.end?'正在计算…':'起点已选好，请点击地图选择终点。');refresh();
    if(state.start&&state.end)await calculate();
  }catch(error){if(revision!==state.revision)return;setBusy(false);status(error.message,true);refresh();}
}
function clear(){state.revision++;setBusy(false);state.start=null;state.end=null;state.route=null;state.pick='start';status('点击地图选择起点。');refresh();}

function fitBox(box){const [x0,y0,x1,y1]=box;state.center=[(x0+x1)/2,(y0+y1)/2];state.scale=Math.min(canvas.clientWidth/Math.max(150,x1-x0),canvas.clientHeight/Math.max(150,y1-y0))*.84;settle();schedule();}
function fitRoute(){if(!state.route)return;const points=[...state.route.path,local(state.start.packet.point),local(state.end.packet.point)];fitBox([Math.min(...points.map(p=>p[0]))-40,Math.min(...points.map(p=>p[1]))-40,Math.max(...points.map(p=>p[0]))+40,Math.max(...points.map(p=>p[1]))+40]);}
function view(){
  if(!state.data)return;
  fitBox(state.data.bounds);
}
function settle(){tilesAfter=performance.now()+170;clearTimeout(tileTimer);tileTimer=setTimeout(schedule,190);}
function zoom(factor,x=canvas.clientWidth/2,y=canvas.clientHeight/2){if(!state.data)return;const old=state.scale;state.scale=Math.max(.0002,Math.min(30,old*factor));state.center[0]+=(x-canvas.clientWidth/2)*(1/old-1/state.scale);state.center[1]-=(y-canvas.clientHeight/2)*(1/old-1/state.scale);settle();schedule();}

function getTile(z,x,y,request){
  const key=z+'/'+x+'/'+y;if(tiles.has(key)){const tile=tiles.get(key);tiles.delete(key);tiles.set(key,tile);return tile;}if(!request)return null;
  const image=new Image(),tile={image,status:'loading'};image.crossOrigin='anonymous';image.referrerPolicy='strict-origin-when-cross-origin';
  image.onload=()=>{tile.status='ready';schedule();};image.onerror=()=>{tile.status='error';schedule();};tiles.set(key,tile);
  image.src=state.data.tile_url.replace('{z}',z).replace('{x}',x).replace('{y}',y);
  if(tiles.size>160)for(const [old,t] of tiles){if(t.status!=='loading'){tiles.delete(old);break;}}
  return tile;
}
function paintTiles(){
  if(!$('basemap').checked){$('tile-status').hidden=true;return;}
  const w=canvas.clientWidth,h=canvas.clientHeight,origin=state.data.origin,z=Math.max(0,Math.min(19,Math.round(Math.log2(WORLD*state.scale/256)))),n=2**z,span=WORLD/n;
  const west=state.center[0]+origin[0]-w/2/state.scale,east=state.center[0]+origin[0]+w/2/state.scale,north=state.center[1]+origin[1]+h/2/state.scale,south=state.center[1]+origin[1]-h/2/state.scale;
  let failed=0;context.globalAlpha=.67;
  for(let x=Math.floor((west+HALF)/span);x<=Math.floor((east+HALF)/span);x++)for(let y=Math.max(0,Math.floor((HALF-north)/span));y<=Math.min(n-1,Math.floor((HALF-south)/span));y++){
    const tile=getTile(z,((x%n)+n)%n,y,performance.now()>=tilesAfter);
    if(tile?.status==='ready'){const p=screen([-HALF+x*span-origin[0],HALF-y*span-origin[1]]),size=span*state.scale;context.drawImage(tile.image,...p,size+.2,size+.2);}else if(tile?.status==='error')failed++;
  }
  context.globalAlpha=1;$('tile-status').hidden=!failed;$('tile-status').textContent='部分底图未载入，通道与选线仍可使用。';
}
function path(points,color,width,dash=[]){context.beginPath();points.forEach((p,i)=>{const q=screen(p);if(i)context.lineTo(...q);else context.moveTo(...q);});context.strokeStyle=color;context.lineWidth=width;context.setLineDash(dash);context.stroke();context.setLineDash([]);}
function marker(p,label,color){const [x,y]=screen(p);context.beginPath();context.arc(x,y,11,0,Math.PI*2);context.fillStyle=color;context.fill();context.strokeStyle='#fff';context.lineWidth=2;context.stroke();context.fillStyle='white';context.font='600 11px sans-serif';context.textAlign='center';context.textBaseline='middle';context.fillText(label,x,y+.5);}
function draw(){
  const w=canvas.clientWidth,h=canvas.clientHeight,ratio=window.devicePixelRatio||1;if(!w||!h)return;
  if(canvas.width!==Math.round(w*ratio)||canvas.height!==Math.round(h*ratio)){canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);}
  context.setTransform(ratio,0,0,ratio,0,0);context.fillStyle='#eff4ea';context.fillRect(0,0,w,h);if(!state.data||!state.center)return;
  paintTiles();context.lineJoin='round';context.lineCap='round';
  const box=[state.center[0]-w/2/state.scale,state.center[1]-h/2/state.scale,state.center[0]+w/2/state.scale,state.center[1]+h/2/state.scale];
  context.strokeStyle='#447e75';context.lineWidth=1;context.globalAlpha=.88;context.beginPath();
  for(const road of state.data.roads){const b=road.bounds;if(b[2]<box[0]||b[0]>box[2]||b[3]<box[1]||b[1]>box[3])continue;road.points.forEach((p,i)=>{const q=screen(p);if(i)context.lineTo(...q);else context.moveTo(...q);});}
  context.stroke();context.globalAlpha=1;
  for(const role of ['start','end'])if(state[role]){
    const point=state[role],snap=selected(point);
    path([local(point.packet.point),local(snap.point)],'#8c9589',1.7,[4,4]);
  }
  if(state.route){
    path(state.route.path,'#fffffff0',6);path(state.route.path,'#d6693e',3.6);
  }
  for(const [role,label,color] of [['start','A','#147c69'],['end','B','#d4683f']])if(state[role])marker(local(state[role].packet.point),label,color);
  const factor=1/Math.cosh((state.center[1]+state.data.origin[1])/R),groundScale=state.scale/factor,target=75/groundScale,power=10**Math.floor(Math.log10(target)),bar=[1,2,5,10].map(x=>x*power).filter(x=>x<=target).pop()||power;
  $('scale').textContent=length(bar);$('scale').style.width=Math.max(1,bar*groundScale)+'px';
}
function schedule(){if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;draw();});}

canvas.addEventListener('wheel',e=>{e.preventDefault();const b=canvas.getBoundingClientRect();zoom(Math.exp(-Math.max(-180,Math.min(180,e.deltaY))*.002),e.clientX-b.left,e.clientY-b.top);},{passive:false});
canvas.addEventListener('pointerdown',e=>{if(!state.data||e.button!==0)return;canvas.setPointerCapture(e.pointerId);state.drag={x:e.clientX,y:e.clientY,center:[...state.center],moved:false};});
canvas.addEventListener('pointermove',e=>{if(!state.drag)return;const d=state.drag,dx=e.clientX-d.x,dy=e.clientY-d.y;if(Math.hypot(dx,dy)>5)d.moved=true;if(d.moved){state.center=[d.center[0]-dx/state.scale,d.center[1]+dy/state.scale];settle();schedule();}});
canvas.addEventListener('pointerup',e=>{const d=state.drag;state.drag=null;if(!d||d.moved)return;const b=canvas.getBoundingClientRect();choose(lonlat(mapPoint(e.clientX-b.left,e.clientY-b.top)));});
for(const event of ['pointercancel','lostpointercapture'])canvas.addEventListener(event,()=>state.drag=null);
new ResizeObserver(()=>{if(state.data&&!state.center)view();schedule();}).observe(canvas.parentElement);
$('zoom-in').addEventListener('click',()=>zoom(1.7));$('zoom-out').addEventListener('click',()=>zoom(1/1.7));$('fit').addEventListener('click',view);$('view').addEventListener('change',view);$('basemap').addEventListener('change',schedule);
$('clear').addEventListener('click',clear);$('fit-route').addEventListener('click',fitRoute);
$('swap').addEventListener('click',()=>{if(state.busy||!state.start||!state.end)return;[state.start,state.end]=[state.end,state.start];calculate();});
for(const role of ['start','end']){
  $('repick-'+role).addEventListener('click',()=>{if(state.busy)return;state.pick=role;status(role==='start'?'请在地图上重新选择起点。':'请在地图上重新选择终点。');refresh();$('instruction').textContent=role==='start'?'点击地图重新选择起点':'点击地图重新选择终点';});
  $(role+'-channel').addEventListener('change',()=>{if(!state[role])return;state[role].choice_id=$(role+'-channel').value;refresh();if(state.start&&state.end)calculate();});
}
$('demo').addEventListener('click',async()=>{
  if(!state.data||state.busy)return;const revision=++state.revision;setBusy(true);status('正在生成示例路线…');
  try{const points=state.data.demo_points,packets=[];for(const point of points)packets.push(await api('/planner/api/snap',{point}));if(revision!==state.revision)return;state.start={packet:packets[0],choice_id:'auto',chosen:packets[0].candidates[0]};state.end={packet:packets[1],choice_id:'auto',chosen:packets[1].candidates[0]};setBusy(false);await calculate(true);}catch(error){if(revision!==state.revision)return;setBusy(false);status(error.message,true);refresh();}
});
$('export').addEventListener('click',()=>{
  if(!state.route)return;const r=state.route,features=[];
  features.push({type:'Feature',properties:{name:'电缆规划通道',road_m:r.road_m,access_m:r.access_m,total_m:r.total_m,source_snapshot:r.source_snapshot,method:r.method,selection:r.selection},geometry:r.points.length>1?{type:'LineString',coordinates:r.points}:{type:'Point',coordinates:r.points[0]}});
  for(const [role,label,snap] of [['start','起点',r.start],['end','终点',r.end]]){features.push({type:'Feature',properties:{name:label,access_m:snap.distance_m,channel:snap.name,level:snap.level},geometry:{type:'Point',coordinates:state[role].packet.point}});if(snap.distance_m>.001)features.push({type:'Feature',properties:{name:label+'接入估算段',length_m:snap.distance_m,projection:snap.projection,position_m:snap.position_m,access_boundary:snap.access_boundary},geometry:{type:'LineString',coordinates:[state[role].packet.point,snap.point]}});}
  const blob=new Blob([JSON.stringify({type:'FeatureCollection',features},null,2)],{type:'application/geo+json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='电缆规划路线.geojson';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
});

(async()=>{
  try{const response=await fetch('/planner/data.json.gz',{headers:{'X-Cable-Token':TOKEN}});if(!response.ok)throw new Error('道路通道未能载入。');const stream=response.body.pipeThrough(new DecompressionStream('gzip'));state.data=JSON.parse(await new Response(stream).text());
    for(const road of state.data.roads){road.bounds=[Math.min(...road.points.map(p=>p[0])),Math.min(...road.points.map(p=>p[1])),Math.max(...road.points.map(p=>p[0])),Math.max(...road.points.map(p=>p[1]))];}
    $('city-name').textContent=state.data.stats.city_name||'城市路网';
    $('network-note').textContent='资料来自用户导入的 OpenStreetMap；规划估算版本。';view();clear();
  }catch(error){status(error.message,true);$('instruction').textContent='道路通道未能载入，请刷新页面';}
})();
