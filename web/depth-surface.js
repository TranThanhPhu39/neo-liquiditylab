'use strict';
/* Bounded 3D projection as SVG: no Python startup, CDN or WebGL required. */
const DepthSurface=(()=>{
  const controllers=new WeakMap(),initial=()=>({yaw:-.65,pitch:.62,zoom:1});
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=n=>new Intl.NumberFormat('vi-VN',{maximumFractionDigits:0}).format(n);
  const clock=n=>`${String(Math.floor(n/3600)).padStart(2,'0')}:${String(Math.floor(n%3600/60)).padStart(2,'0')}`;
  const finite=v=>v!==null&&v!==undefined&&Number.isFinite(v);
  function scene(model,camera=initial()){
    const names=model.side==='both'?['bid','ask']:[model.side||'bid'],points=[];
    for(const s of names){const g=model.grids[s];for(let i=0;i<g.z.length;i++)for(let j=0;j<3;j++){const v=[g.x[i][j],g.y[i][j],g.z[i][j]];if(v.every(finite))points.push(v);}}
    if(!points.length)return '';
    const ranges=[0,1,2].map(k=>{const values=points.map(p=>p[k]),min=k===2?0:Math.min(...values),max=Math.max(...values);return [min,Math.max(max-min,1)];});
    function project(v,normalized=false){
      const n=normalized?v:v.map((x,k)=>(x-ranges[k][0])/ranges[k][1]);
      const x=(n[0]-.5)*1.6,y=(n[1]-.5)*2.2,z=n[2]*1.2;
      const a=x*Math.cos(camera.yaw)-y*Math.sin(camera.yaw),b=x*Math.sin(camera.yaw)+y*Math.cos(camera.yaw);
      return [360+a*150*camera.zoom,315+(b*Math.sin(camera.pitch)-z*Math.cos(camera.pitch))*140*camera.zoom,b*Math.cos(camera.pitch)+z*Math.sin(camera.pitch)];
    }
    const xy=p=>p.slice(0,2).map(x=>x.toFixed(2)).join(','),faces=[],wires=[];
    for(const s of names){
      const g=model.grids[s],color=s==='bid'?'#397c9f':'#338b8d';
      const vertex=(i,j)=>{const v=[g.x[i][j],g.y[i][j],g.z[i][j]];return v.every(finite)?{p:project(v),v}:null;};
      for(let i=0;i<g.z.length-1;i++)for(let j=0;j<2;j++){
        const v=[vertex(i,j),vertex(i,j+1),vertex(i+1,j+1),vertex(i+1,j)];
        if(v.some(p=>!p))continue;
        const volume=v.reduce((n,p)=>n+p.v[2],0)/4,shade=90-55*volume/ranges[2][1],fill=`hsl(${s==='bid'?202:180} 43% ${shade}%)`;
        faces.push({depth:v.reduce((n,p)=>n+p.p[2],0)/4,svg:`<polygon data-side="${s}" points="${v.map(p=>xy(p.p)).join(' ')}" fill="${fill}" stroke="${color}" stroke-opacity=".3" stroke-width=".65"><title>${s.toUpperCase()} · ${esc(g.text[i][j])} · ${fmt(v[0].v[0])} VND · ${fmt(v[0].v[2])} CP</title></polygon>`});
      }
      const stride=Math.max(1,Math.ceil(g.z.length/24));
      for(let i=0;i<g.z.length;i+=stride)for(let j=0;j<3;j++){
        const v=vertex(i,j);if(!v)continue;
        if(j<2){const next=vertex(i,j+1);if(next)wires.push(`<line x1="${v.p[0]}" y1="${v.p[1]}" x2="${next.p[0]}" y2="${next.p[1]}" stroke="${color}" stroke-width=".8"/>`);}
        wires.push(`<circle cx="${v.p[0]}" cy="${v.p[1]}" r="2" fill="${color}"><title>${s.toUpperCase()} · ${esc(g.text[i][j])} · Mức ${j+1} · ${fmt(v.v[0])} VND · ${fmt(v.v[2])} CP</title></circle>`);
      }
    }
    let axes='';const axisText=[];
    const line=(a,b,stroke='#dce6ed')=>{const p=project(a,true),q=project(b,true);return `<line x1="${p[0]}" y1="${p[1]}" x2="${q[0]}" y2="${q[1]}" stroke="${stroke}"/>`;};
    for(let k=0;k<=4;k++){const n=k/4;axes+=line([n,0,0],[n,1,0])+line([0,n,0],[1,n,0])+line([0,1,n],[1,1,n]);}
    const labels=['Giá · VND/CP','Thời gian nguồn · UTC+7',model.cumulative?'Tích lũy · CP':'Khối lượng · CP'];
    for(let k=0;k<3;k++){
      const start=k===0?[0,1,0]:[0,0,0],end=[...start];end[k]=1;axes+=line(start,end,'#748a9b');
      for(let i=0;i<=4;i++){const n=[...start];n[k]=i/4;const p=project(n,true),value=ranges[k][0]+i/4*ranges[k][1];axisText.push(`<text x="${p[0]+(k===2?-10:k===1?-8:0)}" y="${p[1]+(k===2?4:k===1?6:22)}" text-anchor="${k===2||k===1?'end':'middle'}">${k===1?clock(value):fmt(value)}</text>`);}
      const n=[...start];n[k]=1.15;const p=project(n,true);axisText.push(`<text x="${p[0]}" y="${p[1]+(k===2?-12:30)}" text-anchor="middle" fill="#354f63">${labels[k]}</text>`);
    }
    faces.sort((a,b)=>b.depth-a.depth);
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 520" role="img" aria-label="Độ sâu 3D ${esc(model.symbol)} ${esc(model.day)}"><rect width="720" height="520" fill="#fff"/><g>${axes}</g><g class="surface-faces">${faces.map(f=>f.svg).join('')}</g><g class="surface-samples">${wires.join('')}</g><g font-family="Rubik,Arial,sans-serif" font-size="10" fill="#667d90" paint-order="stroke" stroke="white" stroke-width="3" stroke-linejoin="round">${axisText.join('')}</g><text x="22" y="25" font-family="Arial,sans-serif" font-size="12" fill="#397c9f">${esc(model.symbol)} · ${esc(model.day)} · ${names.map(s=>s.toUpperCase()).join(' + ')} · ${model.points} snapshot</text><text x="360" y="507" text-anchor="middle" font-family="Arial,sans-serif" font-size="11" fill="#708292">Kéo để xoay · Cuộn để phóng to · Nhấp đúp để đặt lại</text></svg>`;
  }
  function draw(element,c){const svg=scene(c.model,c.camera);if(svg)element.innerHTML=svg;else element.textContent='Chưa có mức giá và khối lượng hợp lệ để vẽ 3D.';}
  function clear(element,text='Chưa có điểm dữ liệu để vẽ độ sâu 3D.'){const c=controllers.get(element);if(c){c.model=null;c.drag=null;}element.textContent=text;}
  function reset(element){const c=controllers.get(element);if(c?.model){c.camera=initial();draw(element,c);}}
  function render(element,model){
    if(!model?.points){clear(element);return;}
    let c=controllers.get(element);
    if(!c){
      c={camera:initial()};controllers.set(element,c);element.tabIndex=0;
      element.addEventListener('pointerdown',e=>{if(!c.model)return;c.drag={x:e.clientX,y:e.clientY};element.setPointerCapture(e.pointerId);});
      element.addEventListener('pointermove',e=>{if(!c.drag||!c.model)return;c.camera.yaw+=(e.clientX-c.drag.x)/180;c.camera.pitch=Math.max(.15,Math.min(1.3,c.camera.pitch+(e.clientY-c.drag.y)/240));c.drag={x:e.clientX,y:e.clientY};draw(element,c);});
      for(const type of ['pointerup','pointercancel','lostpointercapture'])element.addEventListener(type,()=>{c.drag=null;});
      element.addEventListener('wheel',e=>{if(!c.model||e.ctrlKey)return;e.preventDefault();c.camera.zoom=Math.max(.55,Math.min(2,c.camera.zoom*Math.exp(-e.deltaY*.001)));draw(element,c);},{passive:false});
      element.addEventListener('dblclick',()=>reset(element));
      element.addEventListener('keydown',e=>{if(!c.model)return;if(e.key==='Home'){e.preventDefault();reset(element);}else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();c.camera.yaw+=e.key==='ArrowLeft'?-.1:e.key==='ArrowRight'?.1:0;c.camera.pitch=Math.max(.15,Math.min(1.3,c.camera.pitch+(e.key==='ArrowUp'?-.1:e.key==='ArrowDown'?.1:0)));draw(element,c);}});
    }
    const scope=model.day+'|'+model.symbol;if(c.scope!==scope)c.camera=initial();c.scope=scope;c.model=model;draw(element,c);
  }
  return {render,clear,reset,scene};
})();
if(typeof module!=='undefined')module.exports=DepthSurface;
