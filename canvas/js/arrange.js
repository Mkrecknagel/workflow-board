"use strict";
/* workflow-board canvas arrange.js (module, v2.2). PowerPoint-style Align / Distribute / Match size for cards AND for group lanes (outline = unit).
   Loaded BEFORE select.js so its capture-phase handlers win on lane headers. Relies on window.wb from core.js (board, sel, groupRect, groupBox, render, syncCardPos, snap, GRID, CW).
   Group selection: click a lane header (shift-click adds, marquee over headers adds). A selected group's member cards are all in wb.sel, so dragging any member / the header moves the whole lane.
   Immutable cards may move (position is not a lock); only delete is locked. Everything snaps to the 8px grid; card heights are padded UP to a grid multiple (min-height) so vertical gaps are exact. */
const $=id=>document.getElementById(id);
const wb=()=>window.wb;
const say=(m,cls)=>{const e=$("msg");if(!e)return;e.textContent=m;e.className=cls||"";e.title=m};
const gridUp=n=>Math.ceil(n/wb().GRID)*wb().GRID;
let gsel=new Set(),undoSnap=null,hop=null,panel=null;

/* ---- grid-pad card heights so box edges land on the grid ---- */
/* Pad to a multiple of 2*GRID, not GRID: align-middle puts every card's CENTRE on one line and snaps the card's y to the grid, which is only
   exact when h/2 is itself a grid multiple. Cards of mixed natural height (88 vs 88+run-mode chip=112) would otherwise miss by half a grid step. */
/* F3/D5: idempotent. Clearing and re-setting minHeight on every call wrote the style attribute of every padded card each time; collapse.js and zoom.js watch those
   writes and re-ran, a ~60 Hz loop that detached card buttons mid-click (Enter, Open, never-run, container toggles were "not clickable"). A card already padded
   (offsetHeight == its minHeight and a cached natural height) is skipped, so a stable board produces no DOM writes at all. */
function normalize(){const G2=2*wb().GRID;document.querySelectorAll("#cards .card").forEach(e=>{const mh=e.style.minHeight;if(mh&&e.__wbNat!=null&&e.offsetHeight===parseFloat(mh))return;e.style.minHeight="";const h=e.offsetHeight;e.__wbNat=h;if(h%G2)e.style.minHeight=(Math.ceil(h/G2)*G2)+"px"})}
function afterRender(){if(!wb()||!wb().board)return;normalize();wb().syncCardPos([]);markGroups()}
function markGroups(){valid();document.querySelectorAll("#groups .grp").forEach(e=>e.classList.toggle("gsel",gsel.has(e.dataset.group)))}
const members=g=>wb().board.cards.filter(c=>c.group===g);
function valid(){if(!wb()||!wb().board){gsel.clear();return}const S=wb().sel;
  [...gsel].forEach(g=>{const m=members(g);if(!m.length||!m.every(c=>S.has(c.id)))gsel.delete(g)})}

/* ---- units: selected groups (outline rect) + selected cards not inside a selected group ---- */
function cardRect(c){const e=document.querySelector('.card[data-id="'+c.id+'"]');return{x:c.pos.x,y:c.pos.y,w:e?e.offsetWidth:wb().CW,h:e?e.offsetHeight:112}}
function units(){valid();const b=wb().board,S=wb().sel,out=[],cov=new Set();
  gsel.forEach(g=>{const m=members(g);m.forEach(c=>cov.add(c.id));out.push({g:g,cards:m,r:wb().groupRect(g)})});
  b.cards.forEach(c=>{if(S.has(c.id)&&!cov.has(c.id))out.push({g:null,cards:[c],r:cardRect(c)})});
  return out}
function snapshot(){const b=wb().board;return{b:b,pos:b.cards.map(c=>[c,c.pos.x,c.pos.y]),gs:b.group_styles?JSON.parse(JSON.stringify(b.group_styles)):null}}
function restore(){const s=undoSnap;if(!s||s.b!==wb().board){say("nothing to undo","err");return}
  s.pos.forEach(a=>{a[0].pos.x=a[1];a[0].pos.y=a[2]});
  if(s.gs)s.b.group_styles=s.gs;else delete s.b.group_styles;undoSnap=null;wb().render();say("arrange undone (autosaves)","ok")}
function begin(need,what){const b=wb().board;if(!b){say("open a board first","err");return null}normalize();wb().syncCardPos([]);
  const u=units();if(u.length<need){say(what+": select "+need+"+ cards or groups (click a lane header)","err");return null}return u}
function move(u,dx,dy){u.cards.forEach(c=>{c.pos.x=wb().snap(c.pos.x+dx);c.pos.y=wb().snap(c.pos.y+dy)})}
function done(msg){wb().render();say(msg+" (autosaves; Undo available)","ok")}

function align(kind){const u=begin(2,"align");if(!u)return;undoSnap=snapshot();const S=wb().snap;
  const minX=Math.min(...u.map(a=>a.r.x)),maxR=Math.max(...u.map(a=>a.r.x+a.r.w)),minY=Math.min(...u.map(a=>a.r.y)),maxB=Math.max(...u.map(a=>a.r.y+a.r.h));
  u.forEach(a=>{const r=a.r;let dx=0,dy=0;
    if(kind==="left")dx=minX-r.x;else if(kind==="right")dx=maxR-(r.x+r.w);else if(kind==="center")dx=(minX+maxR)/2-(r.x+r.w/2);
    else if(kind==="top")dy=minY-r.y;else if(kind==="bottom")dy=maxB-(r.y+r.h);else if(kind==="middle")dy=(minY+maxB)/2-(r.y+r.h/2);
    move(a,S(dx),S(dy))});
  done("aligned "+u.length+" "+kind)}

/* axis "h"|"v"; mode "equal" | "fixed"; blocks = grid blocks for fixed */
function distribute(axis,mode,blocks){const need=mode==="equal"?3:2,u=begin(need,"distribute");if(!u)return;undoSnap=snapshot();const S=wb().snap,G=wb().GRID;
  const H=axis==="h",p=a=>H?a.r.x:a.r.y,l=a=>H?a.r.w:a.r.h;u.sort((a,b)=>p(a)-p(b));
  let gap;
  if(mode==="fixed"){const n=Number(blocks);gap=(Number.isFinite(n)&&n>=0?Math.round(n):3)*G}
  else{const tot=p(u[u.length-1])+l(u[u.length-1])-p(u[0]);gap=(tot-u.reduce((s,a)=>s+l(a),0))/(u.length-1)}
  let cur=p(u[0])+l(u[0]);
  for(let i=1;i<u.length;i++){const a=u[i];if(mode==="equal"&&i===u.length-1)break;
    const t=S(cur+gap),d=t-p(a);if(H)move(a,d,0);else move(a,0,d);cur=t+l(a)}
  done("distributed "+u.length+(H?" horizontally":" vertically")+(mode==="fixed"?" ("+(gap/G)+" blocks)":" (equal gaps)"))}

function match(dim){valid();const gs=[...gsel];if(gs.length<2){say("match "+dim+": select 2+ groups (click lane headers)","err");return}
  const b=wb().board;normalize();wb().syncCardPos([]);undoSnap=snapshot();
  const nat=gs.map(g=>wb().groupBox(g)),key=dim==="width"?"w":"h",T=Math.max(...nat.map(n=>n[key]));
  b.group_styles=b.group_styles||{};
  gs.forEach(g=>{b.group_styles[g]=Object.assign({},b.group_styles[g]);const sz=Object.assign({},b.group_styles[g].size);sz[key]=T;b.group_styles[g].size=sz});
  done("matched "+gs.length+" lane "+dim+"s to "+T+"px")}

/* ---- group selection: header click, shift-click, header drag, marquee over headers ---- */
function selectGroup(g,add){const S=wb().sel;valid();
  if(add){if(gsel.has(g)){gsel.delete(g);members(g).forEach(c=>S.delete(c.id))}else{gsel.add(g);members(g).forEach(c=>S.add(c.id))}}
  else{S.clear();gsel.clear();gsel.add(g);members(g).forEach(c=>S.add(c.id))}
  wb().render();const bd=$("inspBody");if(bd)bd.textContent=gsel.size+" group(s), "+S.size+" cards selected."}
const toWorld=ev=>{const r=$("vp").getBoundingClientRect(),v=wb().view;return{x:(ev.clientX-r.left-v.x)/v.k,y:(ev.clientY-r.top-v.y)/v.k}};
let down=0;
window.addEventListener("pointerdown",ev=>{down++;if(down>1){hop=null;return}
  const t=ev.target,hd=t&&t.closest&&t.closest(".grp b");if(!hd||!wb()||!wb().board||ev.button>0)return;
  ev.stopImmediatePropagation();ev.preventDefault();
  const g=hd.parentElement.dataset.group;
  hop={g:g,add:ev.shiftKey,w:toWorld(ev),moved:false,orig:null};
  if(!ev.shiftKey&&!gsel.has(g))selectGroup(g,false);
  hop.orig=[...gsel].flatMap(x=>members(x)).map(c=>[c,c.pos.x,c.pos.y])},true);
window.addEventListener("pointermove",ev=>{if(!hop||down>1)return;const w=toWorld(ev);
  const dx=wb().snap(w.x-hop.w.x),dy=wb().snap(w.y-hop.w.y);if(!Number.isFinite(dx)||!Number.isFinite(dy))return;
  if(!hop.moved&&!dx&&!dy)return;
  if(!hop.moved){hop.moved=true;if(hop.add&&!gsel.has(hop.g)){selectGroup(hop.g,true);hop.orig=[...gsel].flatMap(x=>members(x)).map(c=>[c,c.pos.x,c.pos.y])}}
  hop.orig.forEach(a=>{a[0].pos.x=wb().snap(a[1]+dx);a[0].pos.y=wb().snap(a[2]+dy)});wb().syncCardPos(hop.orig.map(a=>a[0]))},true);
function pend(ev){down=Math.max(0,down-1);
  if(hop){const h=hop;hop=null;if(h.moved){wb().render();say("moved lane(s) (autosaves)")}else if(h.add)selectGroup(h.g,true);return}
  const m=$("marq");if(!m)return;const s=m.style,q={x0:parseFloat(s.left),y0:parseFloat(s.top),x1:parseFloat(s.left)+parseFloat(s.width),y1:parseFloat(s.top)+parseFloat(s.height)};
  setTimeout(()=>{if(!wb().board||![q.x0,q.y0,q.x1,q.y1].every(Number.isFinite))return;const S=wb().sel;let hit=0;
    document.querySelectorAll("#groups .grp").forEach(e=>{const x=parseFloat(e.style.left),y=parseFloat(e.style.top),w=parseFloat(e.style.width);
      /* header band = top 40px of the outline */
      if(x<=q.x1&&x+w>=q.x0&&y<=q.y1&&y+40>=q.y0){const g=e.dataset.group;gsel.add(g);members(g).forEach(c=>S.add(c.id));hit++}});
    if(hit){wb().render();say(gsel.size+" group(s) selected")}},0)}
window.addEventListener("pointerup",pend,true);window.addEventListener("pointercancel",pend,true);

/* ---- UI ---- */
const style=document.createElement("style");
style.textContent=".grp b{pointer-events:auto;cursor:grab;touch-action:none}.grp.gsel{border-style:solid;border-color:var(--sel);box-shadow:0 0 0 2px var(--sel)}"+
 "#arrPanel{display:none;position:fixed;top:48px;right:10px;z-index:30;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;box-shadow:0 4px 16px #0005;width:300px;max-width:calc(100vw - 20px)}"+
 "#arrPanel.open{display:block}#arrPanel h4{margin:6px 0 4px;font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em}#arrPanel h4:first-child{margin-top:0}"+
 "#arrPanel .ar{display:flex;flex-wrap:wrap;gap:4px;align-items:center}#arrPanel button{padding:5px 8px;min-height:30px}#arrPanel input[type=number]{width:56px}"+
 "@media (max-width:699px){#arrPanel{left:8px;right:8px;width:auto;top:46px}#arrPanel button{min-height:38px;flex:1 0 30%}}";
document.head.appendChild(style);
function mk(txt,title,id,fn){const b=document.createElement("button");b.textContent=txt;b.title=title;if(id)b.id=id;b.addEventListener("click",fn);return b}
function row(h,els){const d=document.createElement("div");if(h){const t=document.createElement("h4");t.textContent=h;d.appendChild(t)}const r=document.createElement("div");r.className="ar";els.forEach(b=>r.appendChild(b));d.appendChild(r);return d}
function build(){const bar=$("bar");if(!bar||$("bArrange"))return;
  const p=panel=document.createElement("div");p.id="arrPanel";
  const A=[["left","Left"],["center","Center"],["right","Right"],["top","Top"],["middle","Middle"],["bottom","Bottom"]];
  p.appendChild(row("Align (2+ cards or lanes)",A.map(([k,n])=>mk(n,"Align "+k,"arAlign-"+k,()=>align(k)))));
  const mode=document.createElement("select");mode.id="arMode";[["equal","Equal gaps"],["fixed","Fixed gap"]].forEach(([v,t])=>{const o=document.createElement("option");o.value=v;o.textContent=t;mode.appendChild(o)});
  const n=document.createElement("input");n.type="number";n.id="arBlocks";n.value="3";n.min="0";n.step="1";n.title="gap in 8px grid blocks (fixed gap)";
  const lbl=document.createElement("span");lbl.textContent="blocks";lbl.style.color="var(--mut)";
  p.appendChild(row("Distribute (3+ equal, 2+ fixed)",[mode,n,lbl]));
  p.appendChild(row("",[mk("Horizontal","Distribute horizontally","arDistH",()=>distribute("h",mode.value,n.value)),mk("Vertical","Distribute vertically","arDistV",()=>distribute("v",mode.value,n.value))]));
  p.appendChild(row("Lane size (select lane headers)",[mk("Match width","Make selected lane outlines the same width","arMatchW",()=>match("width")),mk("Match height","Make selected lane outlines the same height","arMatchH",()=>match("height"))]));
  p.appendChild(row("",[mk("Undo arrange","Undo the last arrange action","arUndo",restore),mk("Close","Close","arClose",()=>p.classList.remove("open"))]));
  document.body.appendChild(p);
  const b=mk("Arrange","Align / distribute / match size for selected cards or lane groups","bArrange",()=>p.classList.toggle("open"));
  bar.insertBefore(b,$("msg"))}
build();
/* phone Select mode: turning it on opens the same Arrange actions */
document.addEventListener("click",ev=>{const t=ev.target&&ev.target.closest&&ev.target.closest("#bSelMode");if(!t)return;
  setTimeout(()=>{const ph=window.matchMedia("(max-width:699px)").matches;if(ph&&t.classList.contains("pri"))panel.classList.add("open");else if(ph)panel.classList.remove("open")},0)},true);
/* re-pad card heights + mark selected lanes after every render */
(function(){const c=$("cards");if(!c||!window.MutationObserver)return;let sig="";const hs=()=>[...c.querySelectorAll(".card")].map(e=>e.dataset.id+":"+e.offsetHeight).join("|");
  /* F3/D5: a subtree change (decorator chips, peek/drill buttons) re-pads only when some card HEIGHT actually changed. Re-running afterRender on every subtree write redrew
     #groups, which zoom.js watches, which rewrote card children again: a ~60 Hz loop that detached buttons mid-click. */
  new MutationObserver(recs=>{if(recs.some(r=>r.target===c)){afterRender();sig=hs();return}const n=hs();if(n===sig)return;afterRender();sig=hs()}).observe(c,{childList:true,subtree:true}) /* subtree: decorators (v5 run-mode chip) add children AFTER the card list is rendered and change card height; re-pad to the grid then */})();
window.wbArrange={align:align,distribute:distribute,match:match,undo:restore,setUndo:function(s){undoSnap=s},selectGroup:selectGroup,get gsel(){return gsel}};
