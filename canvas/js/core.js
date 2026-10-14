"use strict";
import {routeAll} from "./router.js";import {badge} from "./readability.js";
/* workflow-board canvas core (module). v2 restructure: state, render, drag, edges, view, insertSubgraph. */
const TYPES={trigger:["⚡","event"],input:["⇥","schema"],skill:["✦","name"],hook:["⚓","event"],tool:["🔧","name"],agent:["🤖","seat"],gate:["⛨","rule"],output:["⇤","schema"],step:["▸","sop"],boundary:["▭","kind"],component:["◆","kind"],datastore:["🛢","kind"],external:["☁","vendor"],person:["👤","role"],resource:["◇","kind"]}; /* resource: fallback card for any library item without a dedicated type */
const V3=["boundary","component","datastore","external","person"]; /* v3 architecture types: structural, no telemetry */
const V3PORTS={component:{in:["uses"],out:["provides"]},datastore:{in:["write"],out:["read"]},external:{in:["call"],out:["event"]},boundary:{in:[],out:[]},person:{in:[],out:[]}};
const V3DEF={boundary:{kind:"machine"},component:{kind:"service"},datastore:{kind:"file",path:"TODO"},external:{vendor:"TODO",protocol:"TODO"},person:{role:"TODO"}};
const REQ={trigger:["event"],input:["schema"],skill:["name"],hook:["event","file"],gate:["kind","rule","verb"],agent:["route","seat","verb"],output:["schema"],tool:["name"],step:[],boundary:["kind"],component:["kind"],datastore:["kind","path"],external:["vendor","protocol"],person:["role"],resource:["kind","name"]};
const CW=192,HH=30,RH=20,STALE_MS=24*3600*1000;
const GRID=8; /* single grid constant: CSS background size (applyView) and every snap use it */
const snap=n=>Math.round(n/GRID)*GRID+0;
const TYPE_COLOR={trigger:"#E69F00",input:"#56B4E9",skill:"#CC79A7",hook:"#F0E442",tool:"#A67C52",agent:"#0072B2",gate:"#D55E00",output:"#009E73",step:"#7A8396",boundary:"#6B7385",component:"#44AA99",datastore:"#AA4499",external:"#999933",person:"#882255",resource:"#5F6B7A"}; /* Okabe-Ito based; icon + label always accompany colour */
const RESERVED=["id","type","ref","pos","immutable","execution_mode","enabled","style","fields"];
const HEX=/^#[0-9a-fA-F]{6}$/;
function textOn(hex){const n=parseInt(hex.slice(1),16),f=v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)};
  const L=.2126*f(n>>16&255)+.7152*f(n>>8&255)+.0722*f(n&255);return(L+.05)/.05>=1.05/(L+.05)?"#111":"#fff"}
const cardColor=c=>(c.style&&HEX.test(c.style.color||"")&&c.style.color)||TYPE_COLOR[c.type]||TYPE_COLOR.resource;
const groupColor=g=>{const s=board&&board.group_styles&&board.group_styles[g];return s&&HEX.test(s.color||"")?s.color:null};
let board=null,boardName=null,sel=new Set(),selEdge=null,view={x:40,y:30,k:1},seen={},drag=null;
const $=id=>document.getElementById(id);
{const l=document.createElement("link");l.rel="stylesheet";l.href="/canvas/style.css";document.head.appendChild(l)} /* v3 styles (boundary boxes, pill) */
const E=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.textContent=h;return e};

function say(m,cls){const e=$("msg");e.textContent=m;e.className=cls||"";e.title=m}
async function api(path,opt){const r=await fetch(path,opt);let j=null;try{j=await r.json()}catch(_){}return{ok:r.ok,status:r.status,j}}
const cardById=id=>board.cards.find(c=>c.id===id);
const epCard=s=>s.slice(0,s.lastIndexOf("."));
const epPort=s=>s.slice(s.lastIndexOf(".")+1);

async function loadBoards(){const r=await api("/boards");const names=(r.j&&r.j.boards)||[];const p=$("picker");p.innerHTML="";
  names.forEach(n=>{const o=E("option",null,n);o.value=n;p.appendChild(o)});
  if(names.length)await openBoard((new URLSearchParams(location.search).get("board")&&names.includes(new URLSearchParams(location.search).get("board")))?new URLSearchParams(location.search).get("board"):names.includes("demo-board")?"demo-board":names[0])}
async function openBoard(n){if(window.wbAutosave&&board&&boardName)window.wbAutosave.beforeSwap(boardName,board); /* v5: pending visual writes go to the OLD board first */
  const r=await api("/board/"+n);if(!r.ok){say("load failed: "+((r.j&&r.j.error)||r.status),"err");return}
  board=r.j;boardName=n;if(window.wbAutosave)window.wbAutosave.opened(n,board);sel=new Set();selEdge=null;$("picker").value=n;if(window.wbTickets)window.wbTickets.boardOpened(n);await loadTelemetry();render();renderInspector();fitView();say("loaded "+n)}
async function loadTelemetry(){const r=await api("/telemetry");seen=(r.ok&&r.j&&r.j.last_seen)||{}}
function isGrey(c){if(V3.includes(c.type)||c.type==="resource")return false;const t=seen[c.id];if(!t)return true;const ms=Date.parse(t);return isNaN(ms)||Date.now()-ms>STALE_MS}

function portY(c,side,name){const i=c.ports[side].indexOf(name);return c.pos.y+HH+6+RH*Math.max(i,0)+RH/2}
/* port geometry is MEASURED from the rendered .pt rows (the dot is CSS-centred on the row edge via --pd), cached per card as offsets from card pos in world px; zoom-independent. */
let portOff={},hov=null,edgeEls=[],ro=null,roQ=0;
/* semantic zoom (zoom.js) hides .card at low zoom (display:none): measure with the cards shown for one synchronous pass so sizes/ports are real at any zoom */
function unlod(fn){const vp=$("vp"),was=vp&&vp.getAttribute("data-lod");if(was==="1")vp.setAttribute("data-lod","0");try{return fn()}finally{if(was==="1")vp.setAttribute("data-lod","1")}}
function measurePorts(){return unlod(measurePorts0)}
function measurePorts0(){const m={},k=view.k||1;
  document.querySelectorAll(".card").forEach(cd=>{const cr=cd.getBoundingClientRect();
    cd.querySelectorAll(".pt[data-port]").forEach(pt=>{const r=pt.getBoundingClientRect(),isIn=pt.classList.contains("in");
      m[(isIn?"in:":"out:")+pt.dataset.port]={dx:((isIn?r.left:r.right)-cr.left)/k,dy:((r.top+r.bottom)/2-cr.top)/k}})});
  const ch=JSON.stringify(m)!==JSON.stringify(portOff);portOff=m;return ch}
function portXY(ep,side){const c=cardById(epCard(ep));if(!c)return null;const o=portOff[side+":"+ep];
  return o?{x:c.pos.x+o.dx,y:c.pos.y+o.dy}:{x:c.pos.x+(side==="out"?CW:0),y:portY(c,side,epPort(ep))}}
function watchSizes(){if(!window.ResizeObserver)return;if(!ro)ro=new ResizeObserver(()=>{if(roQ)return;roQ=requestAnimationFrame(()=>{roQ=0;if(measurePorts())drawEdges()})});
  ro.disconnect();document.querySelectorAll(".card").forEach(c=>ro.observe(c))}

/* v3 child-board summary pill: counts read from the child file on load, cached per session; failure shows "summary unavailable" */
const sumCache={},sumBusy={};
function sumText(id){const v=sumCache[id];if(v===undefined)return"loading summary...";if(v===null)return"summary unavailable";
  return v.cards+" cards · "+v.hooks+" hooks · "+v.gates+" gates · "+v.mode+" · saved "+v.saved}
function sumPaint(id){document.querySelectorAll('.zm-pill[data-drill="'+id+'"]').forEach(e=>{e.textContent=sumText(id);e.classList.toggle("bad",sumCache[id]===null)})}
function sumLoad(id){if(sumCache[id]!==undefined||sumBusy[id])return;sumBusy[id]=1;
  api("/board/"+id).then(r=>{const j=r.ok&&r.j;sumCache[id]=(j&&Array.isArray(j.cards))?{cards:j.cards.length,hooks:j.cards.filter(c=>c.type==="hook").length,gates:j.cards.filter(c=>c.type==="gate").length,mode:j.execution_mode||"observe",saved:String(j.saved_at||"").slice(5,10)||"?"}:null}).catch(()=>{sumCache[id]=null}).then(()=>{delete sumBusy[id];sumPaint(id)})}
function render(){
  const root=$("cards");root.innerHTML="";if(!board)return;
  const bdepth=c=>{let n=0,x=c;while(x&&x.boundary&&n<8){x=cardById(x.boundary);n++}return n};
  const order=board.cards.map((c,i)=>[c,i]).sort((a,b)=>(a[0].type==="boundary"?0:1)-(b[0].type==="boundary"?0:1)||(a[0].type==="boundary"?bdepth(a[0])-bdepth(b[0]):0)||a[1]-b[1]).map(a=>a[0]); /* boundaries first, outermost first: they sit behind everything */
  order.forEach(c=>{
    const d=E("div","card t-"+c.type+(sel.has(c.id)?" sel":"")+(isGrey(c)?" grey":"")+(c.enabled?"":" off"));d.dataset.id=c.id;d.dataset.type=c.type;if(c.type==="boundary"){d.dataset.bkind=(c.ref&&c.ref.kind)||"";if(c.size){d.style.width=c.size.w+"px";d.style.height=c.size.h+"px"}}
    d.style.left=c.pos.x+"px";d.style.top=c.pos.y+"px";
    const col=cardColor(c);d.style.setProperty("--tc",col);d.style.setProperty("--tx",textOn(col));
    const h=E("div","hd");h.appendChild(E("i",null,(TYPES[c.type]||TYPES.resource)[0]));h.appendChild(E("span",null,c.label));
    if(c.immutable){const l=E("small","lock","🔒");l.title="immutable";h.appendChild(l)}
    d.addEventListener("pointerdown",ev=>startDragCard(ev,c));d.addEventListener("pointerenter",()=>{hov=c.id;updateHL()});d.addEventListener("pointerleave",()=>{if(hov===c.id){hov=null;updateHL()}});d.appendChild(h);
    if(c.type==="boundary"){const kd=E("div","bkind",(c.ref&&c.ref.kind)||"boundary");d.appendChild(kd);if(window.wbTickets)window.wbTickets.decorate(d,c);root.appendChild(d);return}
    const n=Math.max(c.ports.in.length,c.ports.out.length,1);const body=E("div");body.style.padding="6px 0";
    for(let i=0;i<n;i++){const r=E("div","row");const a=E("div","pt in",c.ports.in[i]||"");const b=E("div","pt out",c.ports.out[i]||"");
      if(!c.ports.in[i])a.className="";if(!c.ports.out[i])b.className="";
      if(c.ports.in[i])a.dataset.port=c.id+"."+c.ports.in[i];
      if(c.ports.out[i]){b.dataset.port=c.id+"."+c.ports.out[i];b.addEventListener("pointerdown",ev=>startConnect(ev,b.dataset.port))}
      r.appendChild(a);r.appendChild(b);body.appendChild(r)}
    d.appendChild(body);
    if(V3.includes(c.type)){const rk=c.ref&&(c.ref.kind||c.ref.vendor||c.ref.role);if(rk)d.appendChild(E("div","rk",rk))}
    if(c.drill){const sp=E("div","zm-pill",sumText(c.drill));sp.dataset.drill=c.drill;sp.title="child board '"+c.drill+"' (read from the file, never typed)";d.appendChild(sp);sumLoad(c.drill)}
    const f=E("div","ft");if(V3.includes(c.type)){f.appendChild(E("span",null,c.type));f.appendChild(E("span",null,c.execution_mode))}else{f.appendChild(E("span",null,c.type+" · "+c.execution_mode));f.appendChild(E("span",null,seen[c.id]?"seen "+seen[c.id].slice(5,16).replace("T"," "):"no telemetry"))}d.appendChild(f);
    if(window.wbTickets)window.wbTickets.decorate(d,c);root.appendChild(d)});
  drawGroups();applyView();measurePorts();drawEdges();watchSizes()}
/* group outline = member bbox + padding, snapped OUT to the grid (frames on-grid so align/distribute gaps are exact); optional group_styles[g].size {w,h} is a MIN size, anchored top-left (v2.2 arrange) */
const gsnapUp=n=>Math.ceil(n/GRID)*GRID;
function groupBox(k){if(!board)return null;const r=board.cards.filter(c=>c.group===k).map(c=>[c.pos.x,c.pos.y,c.pos.x+CW,c.pos.y+(cardEl(c)||90)]);if(!r.length)return null;
  const x0=Math.min(...r.map(a=>a[0]))-16,y0=Math.min(...r.map(a=>a[1]))-40,x1=gsnapUp(Math.max(...r.map(a=>a[2]))+16),y1=gsnapUp(Math.max(...r.map(a=>a[3]))+16);
  return{x:x0,y:y0,w:x1-x0,h:y1-y0}}
function groupRect(k){const b=groupBox(k);if(!b)return null;const s=board.group_styles&&board.group_styles[k]&&board.group_styles[k].size;
  if(s){if(Number.isFinite(s.w))b.w=Math.max(b.w,gsnapUp(s.w));if(Number.isFinite(s.h))b.h=Math.max(b.h,gsnapUp(s.h))}return b}
function drawGroups(){const g=$("groups");g.innerHTML="";if(!board)return;const names=[];
  board.cards.forEach(c=>{if(c.group&&!names.includes(c.group))names.push(c.group)});
  names.forEach(k=>{const r=groupRect(k);
    const d=E("div","grp");d.dataset.group=k;d.style.cssText="left:"+r.x+"px;top:"+r.y+"px;width:"+r.w+"px;height:"+r.h+"px";
    const gc=groupColor(k);if(gc){d.style.setProperty("--gc",gc);d.style.setProperty("--gx",textOn(gc));d.classList.add("tinted")}
    d.appendChild(E("b",null,k));g.appendChild(d)})}
function cardEl(c){const e=document.querySelector('.card[data-id="'+c.id+'"]');return e?e.offsetHeight:0}

function curve(a,b){const dx=Math.max(40,Math.abs(b.x-a.x)/2);return"M"+a.x+","+a.y+" C"+(a.x+dx)+","+a.y+" "+(b.x-dx)+","+b.y+" "+b.x+","+b.y}
const STUB=18,STUB2=30,FAN=4,NS="http://www.w3.org/2000/svg";
const seg=(p,q,dx)=>" C"+(p.x+dx)+","+p.y+" "+(q.x-dx)+","+q.y+" "+q.x+","+q.y;
/* routing: horizontal-tangent beziers; leave out-ports rightward, enter in-ports from the left; edges sharing a port fan with +-FAN offsets
   over a STUB-long run (path still starts/ends exactly on the port centre); edges from one out-port to one target column merge into a shared trunk. */
let bundleStat={groups:0,edges:0,eligible:0,inMerge:0,laneTrunk:0,portTrunk:0};
function planEdges(){const n=board.edges.length,P=new Array(n).fill(null),og={},ig={},lane={},LM=24;
  board.cards.forEach(c=>{if(!c.group)return;const L=lane[c.group]=lane[c.group]||{x0:1e9,x1:-1e9};L.x0=Math.min(L.x0,c.pos.x);L.x1=Math.max(L.x1,c.pos.x+CW)});
  board.edges.forEach((e,i)=>{const a=portXY(e.from,"out"),b=portXY(e.to,"in");if(!a||!b)return;const p={a:a,b:b,i:i,oo:0,oi:0,mo:false,mi:false,tr:null};P[i]=p;(og[e.from]=og[e.from]||[]).push(p);(ig[e.to]=ig[e.to]||[]).push(p)});
  /* ports shared by >=2 edges merge: edges converge on one point STUB2 away from the port and share the run into it (no fan) */
  let inM=0;Object.keys(og).forEach(k=>{if(og[k].length>1)og[k].forEach(p=>p.mo=true)});
  Object.keys(ig).forEach(k=>{if(ig[k].length>1)ig[k].forEach(p=>{p.mi=true;inM++})});
  /* lane trunk: edges leaving one lane (group) toward one other lane share a trunk between the two lane boxes */
  const lg={},cg={};board.cards.forEach(c=>cg[c.id]=c.group);
  P.forEach(p=>{if(!p)return;const e=board.edges[p.i],f=cg[epCard(e.from)],t=cg[epCard(e.to)];if(!f||!t||f===t)return;(lg[f+""+t]=lg[f+""+t]||[]).push(p)});
  let gs=0,es=0,lt=0,elig=0;
  Object.keys(lg).forEach(k=>{const g=lg[k];if(g.length<2)return;const nm=k.split(""),A=lane[nm[0]],B=lane[nm[1]];
    const mx=A.x1+LM,nx=B.x0-LM;if(nx<mx+40)return;
    const yc=g.reduce((s,p)=>s+(p.a.y+p.b.y)/2,0)/g.length;gs++;lt+=g.length;g.forEach(p=>{p.tr={mx:mx,nx:nx,y:yc,lane:true}})});
  /* same-source-port trunk into one target column (edges not already on a lane trunk) */
  const bg={};P.forEach(p=>{if(!p||p.tr)return;const k=board.edges[p.i].from+"|"+Math.round(p.b.x/260);(bg[k]=bg[k]||[]).push(p)});
  let pt=0;Object.keys(bg).forEach(k=>{const g=bg[k];if(g.length<2)return;const a=g[0].a,mx=a.x+STUB2+14,minB=Math.min(...g.map(p=>p.b.x)),nx=a.x+(minB-a.x)*.6;
    if(nx<mx+24)return;gs++;pt+=g.length;g.forEach(p=>{p.tr={mx:mx,nx:nx,y:a.y}})});
  /* eligible = edges sharing an in-port or a lane pair; bundled = eligible edges that got a merge or a trunk */
  const lp={};P.forEach(p=>{if(!p)return;const e=board.edges[p.i],f=cg[epCard(e.from)],t=cg[epCard(e.to)];if(f&&t&&f!==t){const k=f+""+t;lp[k]=(lp[k]||0)+1}});
  let el=0,bd=0;P.forEach(p=>{if(!p)return;const e=board.edges[p.i],f=cg[epCard(e.from)],t=cg[epCard(e.to)];const inShared=ig[e.to].length>1,laneShared=f&&t&&f!==t&&lp[f+""+t]>1;
    if(inShared||laneShared){el++;if(p.mi||(p.tr&&p.tr.lane))bd++}});
  bundleStat={groups:gs,edges:es+lt+pt,eligible:el,bundled:bd,inMerge:inM,laneTrunk:lt,portTrunk:pt};return P}
function edgeD(p){const a=p.a,b=p.b,so=p.mo?STUB2:STUB,si=p.mi?STUB2:STUB,F={x:a.x+so,y:a.y},E={x:b.x-si,y:b.y};let d="M"+a.x+","+a.y+seg(a,F,so/2);
  if(p.tr){const ty=p.tr.y,M={x:p.tr.mx,y:ty},N={x:p.tr.nx,y:ty};d+=seg(F,M,(M.x-F.x)/2)+" L"+N.x+","+N.y+seg(N,E,Math.max(8,(E.x-N.x)/2))}
  else d+=seg(F,E,Math.max(40,Math.abs(E.x-F.x)/2));
  return d+seg(E,b,si/2)}
/* v6 router: bezier when clean, loop lane for DFS back-edges, orthogonal A* around boxes otherwise (router.js; derived on every render, never saved) */
let routeStat={bezier:0,ortho:0,loop:0,fail:0,loops:[]};
function routeModel(P,quick){if(quick)return[]; /* mid-drag: plain beziers (cheap); the full route is recomputed when the drag ends */
  const els=new Map();document.querySelectorAll("#cards .card").forEach(e=>els.set(e.dataset.id,e));
  const cards=unlod(()=>board.cards.map(c=>{const e=els.get(c.id);return{id:c.id,type:c.type,x:c.pos.x,y:c.pos.y,w:e?e.offsetWidth:0,h:e?e.offsetHeight:0}}));
  const frames=[];document.querySelectorAll("#groups .grp").forEach(g=>{if(g.offsetWidth)frames.push({name:g.dataset.group,x:parseFloat(g.style.left),y:parseFloat(g.style.top),w:parseFloat(g.style.width),h:parseFloat(g.style.height)})});
  const edges=board.edges.map((e,i)=>{const p=P[i];return p?{from:e.from,to:e.to,a:p.a,b:p.b,d0:edgeD(p)}:{from:e.from,to:e.to,a:null,b:null,d0:null}});
  const r=routeAll({cards:cards,frames:frames,edges:edges,quick:quick||!!window.__wbLegacy});routeStat=Object.assign({},r.stat,{loops:r.loops.map(l=>({edge:l.edge,from:l.from,to:l.to,kind:l.kind,lane:l.lane}))});return r.routes}
function updateHL(){if(!board)return;board.edges.forEach((e,i)=>{const p=edgeEls[i];if(!p)return;const f=epCard(e.from),t=epCard(e.to);
  p.classList.toggle("hl",sel.has(f)||sel.has(t)||hov===f||hov===t);p.classList.toggle("sel",selEdge===i)})}
let fullT=0;function schedFull(){clearTimeout(fullT);fullT=setTimeout(()=>{fullT=0;if(board)updateEdges()},180)} /* per-frame drags draw plain beziers; one full route 180ms after the last frame */
function updateEdges(quick){if(!board)return;if(edgeEls.length!==board.edges.length){drawEdges();return}
  const P=planEdges(),R=routeModel(P,quick===true||!!(drag&&drag.t==="card"));P.forEach((p,i)=>{const el=edgeEls[i];if(!el||!p)return;const rt=R[i],d=(rt&&rt.d)||edgeD(p);if(rt&&el.dataset.route!==rt.kind)el.dataset.route=rt.kind;if(el.__d!==d){el.__d=d;el.setAttribute("d",d)}});badge()}
function syncCardPos(cs){cs.forEach(c=>{const el=document.querySelector('.card[data-id="'+c.id+'"]');if(el){el.style.left=c.pos.x+"px";el.style.top=c.pos.y+"px"}});drawGroups();updateEdges(true);schedFull()}
function drawEdges(){const s=$("edges");s.querySelectorAll("path.e").forEach(x=>x.remove());edgeEls=[];if(!board)return;
  const P=planEdges(),R=routeModel(P,!!(drag&&drag.t==="card"));
  board.edges.forEach((e,i)=>{const p=P[i];if(!p){edgeEls.push(null);return}
    const el=document.createElementNS(NS,"path");el.setAttribute("class","e");const rt=R[i];el.dataset.route=rt?rt.kind:"bezier";el.__d=(rt&&rt.d)||edgeD(p);el.setAttribute("d",el.__d);el.setAttribute("marker-end","url(#ah)");
    el.addEventListener("pointerdown",ev=>{ev.stopPropagation();selEdge=i;sel.clear();render();renderInspector()});s.appendChild(el);edgeEls.push(el)});
  updateHL();badge()}
function applyView(){$("world").style.transform="translate("+view.x+"px,"+view.y+"px) scale("+view.k+")";
  /* lines every GRID world px; below 3 screen px per cell draw every Nth line (N*GRID, still on-grid) so the pattern never moires */
  const n=Math.max(1,Math.ceil(3/(GRID*view.k))),g=GRID*n*view.k;$("vp").style.backgroundSize=g+"px "+g+"px";$("vp").style.backgroundPosition=view.x+"px "+view.y+"px"}
function toWorld(ev){const r=$("vp").getBoundingClientRect();return{x:(ev.clientX-r.left-view.x)/view.k,y:(ev.clientY-r.top-view.y)/view.k}}
function fitView(){if(!board||!board.cards.length)return;const r=$("vp").getBoundingClientRect();if(!r.width||!r.height)return;
  const cw=c=>c.size?c.size.w:CW,ch=c=>c.size?c.size.h:(cardEl(c)||110);
  const x0=Math.min(...board.cards.map(c=>c.pos.x)),y0=Math.min(...board.cards.map(c=>c.pos.y));
  const x1=Math.max(...board.cards.map(c=>c.pos.x+cw(c))),y1=Math.max(...board.cards.map(c=>c.pos.y+ch(c)));
  const m=16,w=x1-x0+2*m,h=y1-y0+2*m;
  view.k=Math.max(.02,Math.min(1.2,Math.min(r.width/w,r.height/h)));view.x=(r.width-(x1-x0)*view.k)/2-x0*view.k;view.y=(r.height-(y1-y0)*view.k)/2-y0*view.k;applyView()}

/* ---- drag: move a card ---- */
/* v3 containment: membership is explicit. Dropping a card inside a boundary box sets c.boundary; dragging it out clears it. Never inferred at load. */
function setMembership(c){if(!c||c.type==="boundary"||!board.cards.some(b=>b.type==="boundary"))return;
  const cx=c.pos.x+CW/2,cy=c.pos.y+(cardEl(c)||90)/2;let best=null,ba=1e18;
  board.cards.forEach(b=>{if(b.type!=="boundary"||!b.size||b.id===c.id)return;if(cx>=b.pos.x&&cx<=b.pos.x+b.size.w&&cy>=b.pos.y&&cy<=b.pos.y+b.size.h){const a=b.size.w*b.size.h;if(a<ba){ba=a;best=b}}});
  if(best){if(c.boundary!==best.id)c.boundary=best.id}else if(c.boundary)delete c.boundary}
function startDragCard(ev,c){ev.stopPropagation();ev.preventDefault();selectCard(c.id,true);const w=toWorld(ev);drag={t:"card",c:c,dx:w.x-c.pos.x,dy:w.y-c.pos.y}}
function dragCard(ev){const w=toWorld(ev);drag.c.pos.x=snap(w.x-drag.dx);drag.c.pos.y=snap(w.y-drag.dy);
  const el=document.querySelector('.card[data-id="'+drag.c.id+'"]');if(el){el.style.left=drag.c.pos.x+"px";el.style.top=drag.c.pos.y+"px"}drawGroups();updateEdges()}

/* ---- connect: out port -> in port ---- */
function startConnect(ev,from){ev.stopPropagation();ev.preventDefault();drag={t:"edge",from:from,p:null};
  const p=document.createElementNS("http://www.w3.org/2000/svg","path");p.setAttribute("class","tmp");$("edges").appendChild(p);drag.p=p}
function dragEdge(ev){const a=portXY(drag.from,"out"),b=toWorld(ev);drag.p.setAttribute("d",curve(a,b))}
function finishConnect(ev){const el=document.elementFromPoint(ev.clientX,ev.clientY);const pt=el&&el.closest&&el.closest(".pt.in");
  if(drag.p)drag.p.remove();if(!pt||!pt.dataset.port)return;connect(drag.from,pt.dataset.port)}
function connect(from,to){if(epCard(from)===epCard(to)){say("cannot connect a card to itself","err");return}
  if(board.edges.some(e=>e.from===from&&e.to===to)){say("edge already exists","err");return}
  board.edges.push({from:from,to:to});render();say("connected "+from+" -> "+to)}

/* ---- delete: non-immutable cards/edges only ---- */
const isApproval=c=>!!(c&&c.type==="gate"&&c.ref&&c.ref.kind==="approval");
function deleteSelected(){if(!board)return;
  if(selEdge!=null){const e=board.edges[selEdge];const ends=[epCard(e.from),epCard(e.to)].map(cardById);
    const lock=ends.find(c=>c&&c.immutable);if(lock){say("refused: edge touches immutable card '"+lock.id+"' (locked)","err");return}
    board.edges.splice(selEdge,1);selEdge=null;render();say("edge removed");return}
  if(sel.size){const cs=[...sel].map(cardById).filter(Boolean);if(!cs.length)return;const locked=cs.filter(c=>c.immutable),gone=cs.filter(c=>!c.immutable);
    if(!gone.length){say("refused: '"+locked[0].id+"' is immutable (locked) and cannot be deleted","err");return}
    const appr=gone.filter(isApproval);
    if(appr.length&&!confirm("Delete approval gate "+appr.map(c=>"'"+c.id+"'").join(", ")+"? Approval gates keep a human in the loop; removing one lets the step run without the owner's OK once deployed.")){say("delete cancelled (approval gate kept)");return}
    const ids=new Set(gone.map(c=>c.id));
    const noteOnly=gone.every(c=>c.type==="note");lastDel=noteOnly?{b:board,cards:JSON.parse(JSON.stringify(gone)),edges:board.edges.filter(e=>ids.has(epCard(e.from))||ids.has(epCard(e.to))).map(e=>JSON.parse(JSON.stringify(e)))}:null;
    board.cards=board.cards.filter(x=>!ids.has(x.id));board.edges=board.edges.filter(e=>!ids.has(epCard(e.from))&&!ids.has(epCard(e.to)));
    gone.forEach(c=>sel.delete(c.id));render();renderInspector();
    if(locked.length)say("removed "+gone.length+"; refused immutable: "+locked.map(c=>c.id).join(", "),"err");else say("card '"+gone[0].id+"' removed"+(gone.length>1?" (+"+(gone.length-1)+" more)":"")+(noteOnly?" (autosaves; Undo with Ctrl+Z)":""));if(noteOnly)showUndoDel()}}
let lastDel=null,undoTimer=null;
function undoDelete(){const d=lastDel;if(!d||d.b!==board){say("nothing to undo","err");return false}lastDel=null;d.cards.forEach(c=>{if(!cardById(c.id))board.cards.push(c)});d.edges.forEach(e=>board.edges.push(e));hideUndoDel();render();renderInspector();say("deletion undone (autosaves)","ok");return true}
function hideUndoDel(){const e=$("wb-undo-del");if(e)e.style.display="none";clearTimeout(undoTimer)}
function showUndoDel(){let e=$("wb-undo-del");if(!e){e=document.createElement("div");e.id="wb-undo-del";e.style.cssText="position:fixed;left:12px;bottom:44px;z-index:70;font:12px system-ui,sans-serif;padding:6px 12px;border-radius:12px;background:#243b53;color:#fff;border:1px solid #6aa0d9";e.appendChild(document.createTextNode("note deleted "));const b=document.createElement("button");b.id="wb-undo-del-btn";b.type="button";b.textContent="Undo";b.style.cssText="margin-left:6px;padding:2px 10px;min-height:28px;cursor:pointer";b.onclick=undoDelete;e.appendChild(b);document.body.appendChild(e)}e.style.display="block";clearTimeout(undoTimer);undoTimer=setTimeout(hideUndoDel,15000)}

/* ---- insertSubgraph: paste cards + internal edges with fresh ids, +30/+30 offset, dangling edges dropped ---- */
function insertSubgraph(cards,edges){if(!board)return{};const map={},hex=()=>Math.random().toString(16).slice(2,8).padEnd(6,"0");
  const out=[];(cards||[]).forEach(c0=>{const c=JSON.parse(JSON.stringify(c0));let id;do{id=c.type+"-"+hex()}while(cardById(id)||Object.values(map).includes(id));
    map[c0.id]=id;c.id=id;c.pos={x:snap(c0.pos.x+30),y:snap(c0.pos.y+30)};c.immutable=false;out.push(c)});
  out.forEach(c=>{if(c.boundary){if(map[c.boundary])c.boundary=map[c.boundary];else delete c.boundary}}); /* v3: membership follows the fresh ids; a boundary outside the paste is dropped, never dangling */
  out.forEach(c=>board.cards.push(c));
  (edges||[]).forEach(e=>{const f=map[epCard(e.from)],t=map[epCard(e.to)];if(!f||!t)return;board.edges.push({from:f+"."+epPort(e.from),to:t+"."+epPort(e.to)})});
  sel=new Set(out.map(c=>c.id));selEdge=null;render();renderInspector();return map}

/* ---- palette: add card ---- */
function buildPalette(){const l=$("palList");Object.keys(TYPES).forEach(t=>{const d=E("div","pi");d.style.borderLeft="4px solid "+TYPE_COLOR[t];d.appendChild(E("i",null,TYPES[t][0]));d.appendChild(E("span",null,t));d.addEventListener("click",()=>addCard(t));l.appendChild(d)})}
function addCard(type){if(!board){say("open a board first","err");return}
  let n=1,id;do{id=type.slice(0,4)+"-new-"+(n++)}while(cardById(id));
  const refv=$("pref").value.trim();const ref={};
  REQ[type].forEach((k,i)=>{ref[k]=V3.includes(type)?((i===0&&refv&&k!=="kind")?refv:V3DEF[type][k]):(i===0&&refv)?refv:(k==="verb"?"inject":(k==="kind"?"budget":(k==="seat"?"claude":"TODO")))});
  if(type==="resource"){ref.kind="other";ref.name=refv||"TODO"}
  const ports=V3.includes(type)?JSON.parse(JSON.stringify(V3PORTS[type])):{in:(type==="trigger"||type==="input")?[]:["in"],out:type==="output"?[]:["out"]};
  const r=$("vp").getBoundingClientRect();
  const c={id:id,type:type,label:refv||id,immutable:false,enabled:true,execution_mode:"observe",ref:ref,ports:ports,pos:{x:snap((r.width/2-view.x)/view.k-95),y:snap((r.height/2-view.y)/view.k-40)}};
  if(V3.includes(type)){c.fields={verified:false};if(type==="datastore")c.fields.authority="SoT";if(type==="boundary")c.size={w:320,h:200}}
  board.cards.push(c);if(window.wbNested&&window.wbNested.guardNew)window.wbNested.guardNew(id);$("app").classList.remove("palopen");selectCard(id,false);say("added "+id+" to "+boardName+" - set its ref, then Save")}

/* ---- inspector ---- */
function selectCard(id,quiet){sel=new Set([id]);selEdge=null;if(!quiet){render()}else{document.querySelectorAll(".card").forEach(e=>e.classList.toggle("sel",sel.has(e.dataset.id)));updateHL()}renderInspector()}
function field(lbl,el){const l=E("label",null,lbl);$("inspBody").appendChild(l);$("inspBody").appendChild(el);return el}
function renderInspector(){const b=$("inspBody");b.innerHTML="";
  if(selEdge!=null&&board.edges[selEdge]){const e=board.edges[selEdge];b.textContent=e.from+"  ->  "+e.to;return}
  const c=sel.size===1&&cardById([...sel][0]);if(!c){b.textContent=sel.size>1?sel.size+" cards selected.":"Select a card.";return}
  const ro=c.immutable;
  const inp=(v,fn,dis)=>{const i=E("input");i.value=v;i.disabled=!!dis;i.addEventListener("input",()=>fn(i.value));return i};
  field("id",inp(c.id,()=>{},true));
  field("type",inp(c.type,()=>{},true));
  field("label",inp(c.label,v=>{c.label=v;const h=document.querySelector('.card[data-id="'+c.id+'"] .hd span');if(h)h.textContent=v}));
  const en=E("select");["true","false"].forEach(v=>{const o=E("option",null,v);o.value=v;en.appendChild(o)});en.value=String(c.enabled);en.disabled=ro;en.addEventListener("change",()=>{if(en.value==="false"&&isApproval(c)&&!confirm("Disable approval gate '"+c.id+"'? Once deployed, the step can run without the owner's OK.")){en.value="true";say("disable cancelled (approval gate stays on)");return}c.enabled=en.value==="true";render()});field("enabled"+(ro?" (locked)":""),en);
  const em=E("select");["observe","enforce"].forEach(v=>{const o=E("option",null,v);o.value=v;em.appendChild(o)});em.value=c.execution_mode;em.addEventListener("change",()=>{c.execution_mode=em.value;render()});field("execution_mode",em);
  const rf=E("textarea");rf.value=JSON.stringify(c.ref,null,2);rf.addEventListener("change",()=>{try{c.ref=JSON.parse(rf.value);rf.style.borderColor="";say("ref updated")}catch(e){rf.style.borderColor="#e5484d";say("ref is not valid JSON","err")}});field("ref (JSON)",rf);
  if(c.contract){const ct=E("textarea");ct.value=JSON.stringify(c.contract,null,2);ct.addEventListener("change",()=>{try{c.contract=JSON.parse(ct.value);ct.style.borderColor=""}catch(e){ct.style.borderColor="#e5484d";say("contract is not valid JSON","err")}});field("contract (JSON)",ct)}
  const pr=E("input");pr.value=c.ports.in.join(",")+" | "+c.ports.out.join(",");pr.disabled=ro;pr.title="in ports | out ports, comma separated";
  pr.addEventListener("change",()=>{const p=pr.value.split("|").map(s=>s.split(",").map(x=>x.trim()).filter(Boolean));c.ports={in:p[0]||[],out:p[1]||[]};
    board.edges=board.edges.filter(e=>(epCard(e.from)!==c.id||c.ports.out.includes(epPort(e.from)))&&(epCard(e.to)!==c.id||c.ports.in.includes(epPort(e.to))));render()});field("ports  in | out",pr);
  styleFields(c,ro);if(window.wbTickets)window.wbTickets.inspector(c);
  field("last_seen",inp(seen[c.id]||"none (grey)",()=>{},true))}

/* ---- colour + custom fields (v2.1) ---- */
const mlKeys=new Set();
function recolor(c){const el=document.querySelector('.card[data-id="'+c.id+'"]');if(!el)return;const col=cardColor(c);el.style.setProperty("--tc",col);el.style.setProperty("--tx",textOn(col))}
function styleFields(c,ro){const b=$("inspBody");const tag=ro?" (locked)":"";
  b.appendChild(E("h4",null,"Colour"));
  const row=E("div","crow"),ci=E("input");ci.type="color";ci.id="cardColor";ci.value=cardColor(c).toLowerCase();ci.disabled=!!ro;
  const rs=E("button",null,"Reset");rs.id="cardColorReset";rs.disabled=!!ro;
  ci.addEventListener("input",()=>{c.style=Object.assign({},c.style,{color:ci.value.toLowerCase()});recolor(c)});
  rs.addEventListener("click",()=>{if(c.style){delete c.style.color;if(!Object.keys(c.style).length)delete c.style}ci.value=cardColor(c).toLowerCase();recolor(c)});
  row.append(ci,rs);field("card colour"+tag,row);
  if(c.group){const gr=E("div","crow"),gi=E("input");gi.type="color";gi.id="groupColor";gi.value=(groupColor(c.group)||"#7b8499").toLowerCase();
    const gb=E("button",null,"Reset");gb.id="groupColorReset";
    gi.addEventListener("input",()=>{board.group_styles=board.group_styles||{};board.group_styles[c.group]=Object.assign({},board.group_styles[c.group],{color:gi.value.toLowerCase()});drawGroups()});
    gb.addEventListener("click",()=>{if(board.group_styles&&board.group_styles[c.group]){delete board.group_styles[c.group].color;if(!Object.keys(board.group_styles[c.group]).length)delete board.group_styles[c.group];if(!Object.keys(board.group_styles).length)delete board.group_styles}gi.value="#7b8499";drawGroups()});
    gr.append(gi,gb);field("group colour ("+c.group+")",gr)}
  b.appendChild(E("h4",null,"Custom fields"+tag));
  const box=E("div");box.id="fieldList";b.appendChild(box);
  const isML=(k,v)=>mlKeys.has(c.id+"|"+k)||String(v).includes("\n");
  const draw=()=>{box.innerHTML="";const f=c.fields||{};Object.keys(f).forEach(k=>{const v=f[k];const wrap=E("div","frow");wrap.dataset.key=k;
      wrap.appendChild(E("label",null,k+" ("+(typeof v==="boolean"?"boolean":typeof v==="number"?"number":isML(k,v)?"multiline":"string")+")"));
      let el;if(typeof v==="boolean"){el=E("select");["true","false"].forEach(x=>{const o=E("option",null,x);o.value=x;el.appendChild(o)});el.value=String(v);el.addEventListener("change",()=>{c.fields[k]=el.value==="true"})}
      else if(typeof v==="number"){el=E("input");el.type="number";el.step="any";el.value=v;el.addEventListener("input",()=>{const n=parseFloat(el.value);if(Number.isFinite(n))c.fields[k]=n})}
      else if(isML(k,v)){el=E("textarea");el.value=v;el.style.minHeight="60px";el.addEventListener("input",()=>{c.fields[k]=el.value})}
      else{el=E("input");el.value=v;el.addEventListener("input",()=>{c.fields[k]=el.value})}
      el.disabled=!!ro;el.dataset.fieldKey=k;const rm=E("button","frm","Remove");rm.disabled=!!ro;rm.addEventListener("click",()=>{delete c.fields[k];if(!Object.keys(c.fields).length)delete c.fields;draw()});
      wrap.append(el,rm);box.appendChild(wrap)})};
  draw();
  const add=E("div","fadd"),kn=E("input"),kt=E("select"),kb=E("button",null,"Add field");kn.id="fieldKey";kn.placeholder="field name";kt.id="fieldType";kb.id="fieldAdd";
  ["string","number","boolean","multiline"].forEach(t=>{const o=E("option",null,t);o.value=t;kt.appendChild(o)});kn.disabled=kt.disabled=kb.disabled=!!ro;
  kb.addEventListener("click",()=>{const k=kn.value.trim();
    if(!/^[A-Za-z0-9_.-]{1,40}$/.test(k)){say("field name must be 1-40 chars of A-Z a-z 0-9 _ . -","err");return}
    if(RESERVED.includes(k)){say("field name '"+k+"' is reserved ("+RESERVED.join(", ")+")","err");return}
    if(c.fields&&Object.prototype.hasOwnProperty.call(c.fields,k)){say("field '"+k+"' already exists","err");return}
    const t=kt.value;c.fields=c.fields||{};c.fields[k]=t==="number"?0:t==="boolean"?false:"";if(t==="multiline")mlKeys.add(c.id+"|"+k);kn.value="";draw();say("added field '"+k+"' - Save to persist")});
  add.append(kn,kt,kb);b.appendChild(add)}

/* ---- save / deploy request ---- */
async function saveBoard(){if(!board)return;const A=window.wbAutosave;if(A&&A.beginSave)await A.beginSave(boardName,board);try{await saveBoard2()}finally{if(A&&A.endSave)A.endSave(boardName)}}
async function saveBoard2(){const A=window.wbAutosave,st=A&&A.states.get(boardName),base=st&&st.baseSavedAt,prevSaved=board.saved_at;board.saved_at=new Date().toISOString();
  const r=await api("/board/"+boardName+(base?"?base="+encodeURIComponent(base):""),{method:"PUT",headers:{"Content-Type":"application/json","X-WB-Source":"canvas"},body:JSON.stringify(board,null,2)});
  if(r.ok){if(A){A.saved(boardName,board);if(A.setBase)A.setBase(boardName,board.saved_at)}say("saved "+boardName,"ok")}
  else if(r.status===409&&A&&A.offerRebase){board.saved_at=prevSaved;A.offerRebase(boardName,board,adoptBoard,r.j)}
  else{board.saved_at=prevSaved;say("not saved ("+r.status+"): "+((r.j&&r.j.error)||"error"),"err")}}
function adoptBoard(doc){board=doc;sel=new Set();selEdge=null;render();renderInspector()}
function showDep(d){const lz=$("zm-lenses");const lb=lz?lz.getBoundingClientRect():null;
  d.style.top=(lb&&lb.width>0&&window.innerWidth>699?Math.round(lb.bottom+8):50)+"px";d.style.display="block"}
function hideDep(){const d=$("dep");if(d)d.style.display="none"}
document.addEventListener("pointerdown",e=>{const d=$("dep");if(!d||d.style.display!=="block")return;if(d.contains(e.target)||e.target.closest&&e.target.closest("#bDeploy,#bDeployHelp"))return;hideDep()},true);
document.addEventListener("keydown",e=>{if(e.key==="Escape")hideDep()},true);
function deployRequest(){if(!board)return;const d=$("dep");d.innerHTML="";d.appendChild(E("div",null,"Ask the agent to deploy this board — it will start following these rules. Save first if you changed any behaviour."));
  const sm=E("div",null,"Command, if you need it:");sm.style.cssText="font-size:11px;color:var(--mut);margin-top:6px";d.appendChild(sm);
  d.appendChild(E("code",null,"/board deploy "+boardName));const x=E("button",null,"Close");x.onclick=hideDep;d.appendChild(x);showDep(d);
  say("deploy requested - ask the agent to deploy; this page never writes active.json")}

/* ---- global pointer / keys ---- */
const ptrs=new Map();let pinch=null;
const two=()=>{const a=[...ptrs.values()];return{d:Math.hypot(a[0].x-a[1].x,a[0].y-a[1].y)||1,x:(a[0].x+a[1].x)/2,y:(a[0].y+a[1].y)/2}};
$("vp").addEventListener("pointerdown",ev=>{ptrs.set(ev.pointerId,{x:ev.clientX,y:ev.clientY});
  if(ptrs.size===2){if(drag&&drag.t==="edge"&&drag.p)drag.p.remove();drag=null;$("vp").classList.remove("pan");const t=two(),r=$("vp").getBoundingClientRect();
    pinch={d:t.d,k:view.k,wx:(t.x-r.left-view.x)/view.k,wy:(t.y-r.top-view.y)/view.k};ev.stopPropagation();ev.preventDefault();return}
  if(ptrs.size>2){ev.stopPropagation();return}},true);
$("vp").addEventListener("pointerdown",ev=>{if(ev.target.closest(".card"))return;drag={t:"pan",x:ev.clientX-view.x,y:ev.clientY-view.y};$("vp").classList.add("pan");sel.clear();selEdge=null;render();renderInspector()});
window.addEventListener("pointermove",ev=>{if(ptrs.has(ev.pointerId))ptrs.set(ev.pointerId,{x:ev.clientX,y:ev.clientY});
  if(pinch&&ptrs.size>=2){const t=two(),r=$("vp").getBoundingClientRect();const k=Math.max(.02,Math.min(2.5,pinch.k*t.d/pinch.d));
    view.k=k;view.x=t.x-r.left-pinch.wx*k;view.y=t.y-r.top-pinch.wy*k;applyView();return}
  if(!drag)return;if(drag.t==="card")dragCard(ev);else if(drag.t==="edge")dragEdge(ev);else if(drag.t==="pan"){view.x=ev.clientX-drag.x;view.y=ev.clientY-drag.y;applyView()}});
function ptrEnd(ev){ptrs.delete(ev.pointerId);if(pinch){if(ptrs.size<2)pinch=null;return}
  if(!drag)return;const wasCard=drag.t==="card";if(drag.t==="card"&&ev.type==="pointerup")setMembership(drag.c);if(drag.t==="edge"&&ev.type==="pointerup")finishConnect(ev);else if(drag.t==="edge"&&drag.p)drag.p.remove();$("vp").classList.remove("pan");drag=null;if(wasCard)updateEdges()}
window.addEventListener("pointerup",ptrEnd);window.addEventListener("pointercancel",ptrEnd);
["gesturestart","gesturechange","gestureend"].forEach(n=>document.addEventListener(n,e=>e.preventDefault()));
$("bPal").onclick=()=>{$("app").classList.toggle("palopen");$("app").classList.remove("inspopen")};
$("bInsp").onclick=()=>{$("app").classList.toggle("inspopen");$("app").classList.remove("palopen")};
$("vp").addEventListener("wheel",ev=>{ev.preventDefault();const r=$("vp").getBoundingClientRect(),mx=ev.clientX-r.left,my=ev.clientY-r.top;
  const k=Math.max(.02,Math.min(2.5,view.k*(ev.deltaY<0?1.1:1/1.1)));view.x=mx-(mx-view.x)*(k/view.k);view.y=my-(my-view.y)*(k/view.k);view.k=k;applyView()},{passive:false});
window.addEventListener("keydown",ev=>{const t=ev.target.tagName;if(t==="INPUT"||t==="TEXTAREA"||t==="SELECT")return;if(ev.key==="Delete"||ev.key==="Backspace"){ev.preventDefault();deleteSelected()}});
window.addEventListener("keydown",ev=>{const t=ev.target.tagName;if(t==="INPUT"||t==="TEXTAREA"||t==="SELECT")return;if((ev.ctrlKey||ev.metaKey)&&!ev.shiftKey&&(ev.key==="z"||ev.key==="Z")&&lastDel){ev.preventDefault();undoDelete()}});
$("bDeployHelp").onclick=()=>{const d=$("dep");d.innerHTML="";d.appendChild(E("div",null,"Deploy = the agent starts following this board's behaviour."));d.appendChild(E("div",null,"Visual changes (moving, colours, notes) autosave and never need a deploy. Behaviour changes: Save, then the owner runs the deploy command."));const x=E("button",null,"Close");x.onclick=hideDep;d.appendChild(x);showDep(d)};
$("bSave").onclick=saveBoard;$("bDeploy").onclick=deployRequest;$("bFit").onclick=fitView;$("picker").onchange=e=>openBoard(e.target.value);
buildPalette();loadBoards().catch(e=>say("init failed: "+e,"err"));

window.wb={unlod:unlod,adoptBoard:adoptBoard,openBoard:openBoard,fitView:fitView,setView:v=>{if(!v)return;if(Number.isFinite(v.k))view.k=Math.max(.02,Math.min(2.5,v.k));if(Number.isFinite(v.x))view.x=v.x;if(Number.isFinite(v.y))view.y=v.y;applyView()},V3:V3,get board(){return board},get boardName(){return boardName},get sel(){return sel},get view(){return view},insertSubgraph:insertSubgraph,render:render,drawEdges:drawEdges,updateEdges:updateEdges,syncCardPos:syncCardPos,planEdges:planEdges,get bundleStat(){return bundleStat},get routeStat(){return routeStat},GRID:GRID,CW:CW,snap:snap,groupBox:groupBox,groupRect:groupRect,drawGroups:drawGroups,TYPE_COLOR:TYPE_COLOR,TYPES:TYPES,RESERVED:RESERVED,textOn:textOn};
