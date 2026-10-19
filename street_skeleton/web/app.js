"use strict";

const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="street-token"]').content;
const canvases = [$('source-map'), $('result-map')];
const state = {data:null, mode:'compare', file:null, busy:false, center:[0,0], scale:1, drag:null};
let pendingFrame = false;

function status(message, error=false, busy=false) {
  $('status').textContent = message;
  $('status').className = 'status' + (error ? ' error' : '') + (busy ? ' busy' : '');
}

function setBusy(busy) {
  state.busy = busy;
  $('run').disabled = busy || !state.file;
  $('file').disabled = busy;
  $('tolerance').disabled = busy;
}

function prepare(paths) {
  return paths.map(points => {
    let x0=Infinity, y0=Infinity, x1=-Infinity, y1=-Infinity;
    for (const [x,y] of points) {x0=Math.min(x0,x); y0=Math.min(y0,y); x1=Math.max(x1,x); y1=Math.max(y1,y);}
    return {points, bounds:[x0,y0,x1,y1]};
  });
}

function load(data) {
  state.data = {...data, source:prepare(data.source), roads:prepare(data.roads)};
  $('empty').hidden = true;
  $('metrics').hidden = false;
  $('road-count').textContent = data.stats.roads.toLocaleString();
  $('junction-count').textContent = data.stats.intersections.toLocaleString();
  $('length').textContent = data.stats.length_km.toLocaleString() + ' km';
  $('review-note').hidden = !data.stats.review_points && !data.stats.incomplete_ways_skipped;
  const notes=[];
  if (data.stats.review_points) notes.push(`${data.stats.review_points} 处桥隧连接待核查，已标红`);
  if (data.stats.incomplete_ways_skipped) notes.push(`${data.stats.incomplete_ways_skipped} 条道路因下载缺少节点而跳过`);
  $('review-note').textContent = notes.join('；');
  if (!window.PRELOADED) $('exports').hidden = false;
  setMode(state.mode);
}

function setMode(mode) {
  state.mode = mode;
  for (const button of document.querySelectorAll('[data-mode]')) {
    button.classList.toggle('active', button.dataset.mode === mode);
    button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  }
  $('source-pane').style.display = state.data && mode !== 'result' ? 'block' : 'none';
  $('result-pane').style.display = state.data && mode !== 'source' ? 'block' : 'none';
  fit();
}

function activeCanvas() { return state.mode === 'source' ? canvases[0] : canvases[1]; }

function fit() {
  if (!state.data) return;
  const [x0,y0,x1,y1] = state.data.bounds;
  const canvas = activeCanvas();
  state.center = [(x0+x1)/2, (y0+y1)/2];
  state.scale = Math.max(.00001, Math.min(canvas.clientWidth/Math.max(1,x1-x0), canvas.clientHeight/Math.max(1,y1-y0))*.88);
  schedule();
}

function project(x,y,w,h) {
  return [(x-state.center[0])*state.scale+w/2, (state.center[1]-y)*state.scale+h/2];
}

function draw(canvas, source) {
  const ratio = window.devicePixelRatio || 1;
  const w=canvas.clientWidth, h=canvas.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== Math.round(w*ratio) || canvas.height !== Math.round(h*ratio)) {
    canvas.width=Math.round(w*ratio); canvas.height=Math.round(h*ratio);
  }
  const context=canvas.getContext('2d');
  context.setTransform(ratio,0,0,ratio,0,0);
  context.fillStyle='#f4f7f1'; context.fillRect(0,0,w,h);
  if (!state.data) return;
  const view=[state.center[0]-w/2/state.scale,state.center[1]-h/2/state.scale,
              state.center[0]+w/2/state.scale,state.center[1]+h/2/state.scale];
  context.lineWidth=source ? .85 : 1.15;
  context.strokeStyle=source ? '#91a39d' : '#238b7d';
  context.lineJoin='round'; context.lineCap='round'; context.beginPath();
  for (const {points,bounds:b} of (source ? state.data.source : state.data.roads)) {
    if (b[2]<view[0] || b[0]>view[2] || b[3]<view[1] || b[1]>view[3]) continue;
    points.forEach(([x,y],i) => { const p=project(x,y,w,h); if (!i) context.moveTo(...p); else context.lineTo(...p); });
  }
  context.stroke();
  if (!source && $('junctions').checked) {
    context.fillStyle='#d58153';
    context.beginPath();
    const radius=state.scale > .5 ? 2.5 : 1.5;
    for (const [x,y] of state.data.intersections) {
      const [a,b]=project(x,y,w,h);
      if (a < -5 || a > w+5 || b < -5 || b > h+5) continue;
      context.moveTo(a+radius,b); context.arc(a,b,radius,0,Math.PI*2);
    }
    context.fill();
  }
  if (!source) {
    context.strokeStyle='#c45242'; context.lineWidth=2;
    for (const [x,y] of state.data.review) {
      const [a,b]=project(x,y,w,h);
      context.beginPath(); context.arc(a,b,6,0,Math.PI*2); context.stroke();
    }
  }
}

function schedule() {
  if (pendingFrame) return;
  pendingFrame=true;
  requestAnimationFrame(() => {
    pendingFrame=false;
    draw(canvases[0],true); draw(canvases[1],false);
    if (state.data) {
      const target=90/state.scale;
      const magnitude=10**Math.floor(Math.log10(target));
      const length=[1,2,5,10].map(n=>n*magnitude).filter(n=>n<=target).pop() || magnitude;
      $('scale').hidden=false;
      $('scale').style.width=`${Math.max(1,length*state.scale)}px`;
      $('scale').textContent=length>=1000 ? `${length/1000} km` : `${length} m`;
    }
  });
}

function zoom(factor, x, y, canvas=activeCanvas()) {
  if (!state.data) return;
  const w=canvas.clientWidth,h=canvas.clientHeight;
  x=x ?? w/2; y=y ?? h/2;
  const old=state.scale;
  state.scale=Math.min(30,Math.max(.00001,old*factor));
  state.center[0]+=(x-w/2)*(1/old-1/state.scale);
  state.center[1]-=(y-h/2)*(1/old-1/state.scale);
  $('tooltip').hidden=true;
  schedule();
}

for (const canvas of canvases) {
  canvas.addEventListener('wheel', event => {
    event.preventDefault();
    const box=canvas.getBoundingClientRect();
    zoom(Math.exp(-Math.min(200,Math.max(-200,event.deltaY))*.002), event.clientX-box.left,event.clientY-box.top,canvas);
  }, {passive:false});
  canvas.addEventListener('pointerdown', event => {
    if (!state.data) return;
    canvas.setPointerCapture(event.pointerId);
    state.drag={x:event.clientX,y:event.clientY,center:[...state.center]};
    $('tooltip').hidden=true;
  });
  canvas.addEventListener('pointermove', event => {
    if (state.drag) {
      state.center=[state.drag.center[0]-(event.clientX-state.drag.x)/state.scale,
                    state.drag.center[1]+(event.clientY-state.drag.y)/state.scale];
      schedule();
      return;
    }
    if (!state.data || canvas !== canvases[1] || !$('junctions').checked) return;
    const box=canvas.getBoundingClientRect(), mx=event.clientX-box.left,my=event.clientY-box.top;
    let best=null,distance=64;
    for (const point of state.data.intersections) {
      const [x,y]=project(point[0],point[1],canvas.clientWidth,canvas.clientHeight);
      const d=(mx-x)**2+(my-y)**2;
      if (d<distance) {distance=d;best=point;}
    }
    $('tooltip').hidden=!best;
    if (best) {
      const area=$('map-area').getBoundingClientRect();
      $('tooltip').textContent=`交叉点 ${best[2]} · ${best[3]} 条连接`;
      $('tooltip').style.left=`${Math.min(event.clientX-area.left+12, area.width-170)}px`;
      $('tooltip').style.top=`${Math.max(0,event.clientY-area.top-32)}px`;
    }
  });
  for (const event of ['pointerup','pointercancel','lostpointercapture']) canvas.addEventListener(event,()=>{state.drag=null;});
  canvas.addEventListener('pointerleave',()=>{$('tooltip').hidden=true;});
}

function choose(file) {
  if (state.busy || !file) return;
  if (!file.name.toLowerCase().endsWith('.pbf')) return status('请选择 .osm.pbf 文件',true);
  state.file=file;
  $('file-label').textContent=file.name;
  $('run').disabled=false;
  status(`${(file.size/1024/1024).toFixed(1)} MB · 在本机处理`);
}

async function api(url, options={}) {
  const response=await fetch(url,{...options,headers:{...options.headers,'X-Street-Token':token}});
  if (!response.ok) {
    let message=`请求失败 (${response.status})`;
    try {message=(await response.json()).error || message;} catch (_) {}
    throw new Error(message);
  }
  return response;
}

async function poll() {
  try {
    const remote=await (await api('/api/state')).json();
    setBusy(remote.busy);
    status(remote.message,remote.error,remote.busy);
    if (remote.busy) return setTimeout(poll,1200);
    if (remote.ready) load(await (await api('/api/result')).json());
  } catch (error) {setBusy(false);status(error.message,true);}
}

$('file').addEventListener('change',event=>choose(event.target.files[0]));
$('drop-zone').addEventListener('dragover',event=>{event.preventDefault();$('drop-zone').classList.add('over');});
$('drop-zone').addEventListener('dragleave',()=>$('drop-zone').classList.remove('over'));
$('drop-zone').addEventListener('drop',event=>{event.preventDefault();$('drop-zone').classList.remove('over');choose(event.dataTransfer.files[0]);});
$('tolerance').addEventListener('input',()=>$('tolerance-value').textContent=$('tolerance').value);
$('run').addEventListener('click',async()=>{
  if (!state.file || state.busy) return;
  setBusy(true); status('正在读取文件',false,true); $('exports').hidden=true;
  try {
    await api('/api/run?tolerance='+$('tolerance').value,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:state.file});
    poll();
  } catch(error) {setBusy(false);status(error.message,true);}
});
for (const button of document.querySelectorAll('[data-mode]')) button.addEventListener('click',()=>setMode(button.dataset.mode));
for (const button of document.querySelectorAll('[data-download]')) button.addEventListener('click',async()=>{
  try {
    button.disabled=true;
    const blob=await (await api('/api/download?file='+button.dataset.download)).blob();
    const url=URL.createObjectURL(blob), link=document.createElement('a');
    link.href=url;link.download=button.dataset.download;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
  } catch(error) {status(error.message,true);} finally {button.disabled=false;}
});
$('junctions').addEventListener('change',schedule);
$('fit').addEventListener('click',fit);
$('zoom-in').addEventListener('click',()=>zoom(1.5));
$('zoom-out').addEventListener('click',()=>zoom(1/1.5));
new ResizeObserver(()=>{if (state.data) fit();}).observe($('map-area'));

if (window.PRELOADED) {
  document.body.classList.add('preview');
  $('import-panel').hidden=true;
  load(window.PRELOADED);
} else poll();
