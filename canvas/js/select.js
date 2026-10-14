"use strict";
/* workflow-board canvas select.js (module). Multi-select (shift-click, marquee), group drag, clipboard (Ctrl+C/X/V + buttons).
   Self-installing: relies only on window.wb ({board, sel(Set, mutated in place), view, insertSubgraph, render}) from core.js.
   Capture-phase window listeners run BEFORE core's handlers, so we take over only the gestures we own and let the rest pass. */
const KEY="wb.clipboard",CW=192;
const $=id=>document.getElementById(id);
const wb=()=>window.wb;
const say=(m,cls)=>{const e=$("msg");if(!e)return;e.textContent=m;e.className=cls||"";e.title=m};
const epCard=s=>s.slice(0,s.lastIndexOf("."));
let selectMode=false,down=0,op=null,pasteN=0,lastClipStamp=null;

function toWorld(ev){const r=$("vp").getBoundingClientRect(),v=wb().view;return{x:(ev.clientX-r.left-v.x)/v.k,y:(ev.clientY-r.top-v.y)/v.k}}
function cardH(c){const e=document.querySelector('.card[data-id="'+c.id+'"]');return e?e.offsetHeight:110}
function inspNote(){const b=$("inspBody");if(b&&wb().sel.size>1)b.textContent=wb().sel.size+" cards selected."}
function refresh(){wb().render();inspNote()}

/* ---- pointer: shift-click, group drag, marquee ---- */
window.addEventListener("pointerdown",ev=>{
  down++;if(down>1){op=null;return}            /* multi-touch: abandon, let core pinch */
  if(!wb()||!wb().board||ev.button>0)return;
  const t=ev.target;if(!t||!t.closest||!t.closest("#vp"))return;
  const cardEl=t.closest(".card");
  if(cardEl){
    if(t.closest(".pt.out"))return;             /* port drag = connect, core owns it */
    const id=cardEl.dataset.id,S=wb().sel;
    if(ev.shiftKey){ev.stopPropagation();ev.preventDefault();if(S.has(id))S.delete(id);else S.add(id);refresh();return}
    if(S.has(id)&&S.size>1){                    /* group drag */
      ev.stopPropagation();ev.preventDefault();
      const cs=[...S].map(i=>wb().board.cards.find(c=>c.id===i)).filter(Boolean);
      const mv=cs.filter(c=>!c.immutable),lk=cs.filter(c=>c.immutable);
      if(lk.length)say("group move: skipping immutable "+lk.map(c=>c.id).join(", "),"err");
      op={t:"gdrag",w:toWorld(ev),orig:mv.map(c=>[c,c.pos.x,c.pos.y])};
    }
    return}
  if(ev.shiftKey||selectMode){                  /* marquee on empty space (also starts over an edge line) */
    ev.stopPropagation();ev.preventDefault();
    const box=document.createElement("div");box.id="marq";
    box.style.cssText="position:absolute;border:1.5px dashed var(--sel);background:#2f6bff22;pointer-events:none;z-index:5";
    $("world").appendChild(box);
    op={t:"marq",w:toWorld(ev),box:box,add:ev.shiftKey,base:new Set(wb().sel)};
  }
},true);

window.addEventListener("pointermove",ev=>{
  if(!op||down>1)return;
  const w=toWorld(ev);
  if(op.t==="gdrag"){
    const dx=wb().snap(w.x-op.w.x),dy=wb().snap(w.y-op.w.y);
    if(!Number.isFinite(dx)||!Number.isFinite(dy))return;
    op.orig.forEach(a=>{a[0].pos.x=wb().snap(a[1]+dx);a[0].pos.y=wb().snap(a[2]+dy)});
    op.moved=true;wb().syncCardPos(op.orig.map(a=>a[0]))}
  else if(op.t==="marq"){
    const x0=Math.min(w.x,op.w.x),y0=Math.min(w.y,op.w.y),x1=Math.max(w.x,op.w.x),y1=Math.max(w.y,op.w.y);
    const s=op.box.style;s.left=x0+"px";s.top=y0+"px";s.width=(x1-x0)+"px";s.height=(y1-y0)+"px";op.r=[x0,y0,x1,y1]}
},true);

function end(ev){
  down=Math.max(0,down-1);
  if(!op)return;
  const o=op;op=null;
  if(o.t==="marq"){
    o.box.remove();const S=wb().sel;S.clear();if(o.add)o.base.forEach(i=>S.add(i));
    if(o.r){const[x0,y0,x1,y1]=o.r;
      wb().board.cards.forEach(c=>{const cx1=c.pos.x+CW,cy1=c.pos.y+cardH(c);
        if(c.pos.x<=x1&&cx1>=x0&&c.pos.y<=y1&&cy1>=y0)S.add(c.id)})}
    refresh();say(S.size+" selected");
  }else if(o.t==="gdrag"&&o.moved){wb().render();say("moved "+o.orig.length+" cards (autosaves)")}
}
window.addEventListener("pointerup",end,true);
window.addEventListener("pointercancel",end,true);

/* ---- clipboard ---- */
function copySel(quiet){
  const b=wb().board;if(!b)return null;const ids=new Set(wb().sel);
  const cards=b.cards.filter(c=>ids.has(c.id));if(!cards.length){if(!quiet)say("nothing selected to copy","err");return null}
  const edges=b.edges.filter(e=>ids.has(epCard(e.from))&&ids.has(epCard(e.to)));
  const stamp=Date.now()+"";
  const payload={v:1,from:b.board||null,stamp:stamp,cards:JSON.parse(JSON.stringify(cards)),edges:JSON.parse(JSON.stringify(edges))};
  try{localStorage.setItem(KEY,JSON.stringify(payload))}catch(e){say("clipboard unavailable: "+e,"err");return null}
  pasteN=0;lastClipStamp=stamp;
  if(!quiet)say("copied "+cards.length+" card(s), "+edges.length+" edge(s)","ok");return payload}
function pasteClip(){
  const b=wb().board;if(!b){say("open a board first","err");return}
  let p=null;try{p=JSON.parse(localStorage.getItem(KEY)||"null")}catch(e){}
  if(!p||!Array.isArray(p.cards)||!p.cards.length){say("clipboard empty","err");return}
  if(p.stamp!==lastClipStamp){pasteN=0;lastClipStamp=p.stamp}
  pasteN++;const off=30*(pasteN-1);                 /* insertSubgraph adds +30/+30; stack repeat pastes */
  const cards=p.cards.map(c=>Object.assign({},c,{pos:{x:c.pos.x+off,y:c.pos.y+off}}));
  const map=wb().insertSubgraph(cards,p.edges||[]);
  const now=new Date().toISOString();
  Object.values(map||{}).forEach(id=>{const c=b.cards.find(x=>x.id===id);if(c)c.pasted_at=now});
  refresh();say("pasted "+Object.keys(map||{}).length+" card(s) from "+(p.from||"clipboard")+" (Save to keep)","ok")}
function cutSel(){
  if(!copySel(true))return;
  document.body.dispatchEvent(new KeyboardEvent("keydown",{key:"Delete",bubbles:true}));  /* core's deleteSelected: immutable-safe */
}
window.addEventListener("keydown",ev=>{
  const t=ev.target&&ev.target.tagName;if(t==="INPUT"||t==="TEXTAREA"||t==="SELECT")return;
  if(!(ev.ctrlKey||ev.metaKey)||ev.altKey)return;const k=ev.key.toLowerCase();
  if(k==="c"){ev.preventDefault();copySel(false)}
  else if(k==="x"){ev.preventDefault();cutSel()}
  else if(k==="v"){ev.preventDefault();pasteClip()}
  else if(k==="a"){ev.preventDefault();const S=wb().sel;wb().board.cards.forEach(c=>S.add(c.id));refresh()}
});

/* ---- toolbar buttons (phone-friendly) ---- */
function mkBtn(id,txt,title,fn){const b=document.createElement("button");b.id=id;b.textContent=txt;b.title=title;b.addEventListener("click",fn);return b}
(function install(){
  const bar=$("bar"),msg=$("msg");if(!bar){return}
  const bs=mkBtn("bSelMode","Select","Marquee mode: drag on empty space to box-select (or hold Shift)",()=>{selectMode=!selectMode;bs.classList.toggle("pri",selectMode);say(selectMode?"select mode: drag empty space to box-select":"pan mode")});
  const bc=mkBtn("bCopy","Copy","Copy selected cards (Ctrl+C)",()=>copySel(false));
  const bp=mkBtn("bPaste","Paste","Paste clipboard (Ctrl+V)",pasteClip);
  [bs,bc,bp].forEach(b=>bar.insertBefore(b,msg));
})();
