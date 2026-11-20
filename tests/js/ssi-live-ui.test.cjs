const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('realtime page boots, paints actual heatmap/bubbles and can pause without stopping collector',async()=>{
  const html=fs.readFileSync('web/monitor/realtime.html','utf8'),elements=new Map();let rectangles=0,arcs=0;
  const context=new Proxy({},{get:(t,k)=>t[k]??(()=>{if(k==='fillRect')rectangles++;if(k==='arc')arcs++;}),set:(t,k,v)=>(t[k]=v,true)});
  class Element{
    constructor(id=''){this.id=id;this.value='';this.children=[];this.hidden=true;this.disabled=false;this.checked=true;this.dataset={};this.classList={toggle(){}};}
    replaceChildren(...items){this.children=items;if(this.id==='run')this.value='';} append(...items){this.children.push(...items);} add(option){this.children.push(option);if(!this.value)this.value=option.value;}
    getBoundingClientRect(){return {width:900,height:360,left:0,top:0};} getContext(){return context;}
  }
  for(const match of html.matchAll(/id="([^"]+)"/g))elements.set(match[1],new Element(match[1]));
  for(const [id,value] of Object.entries({symbol:'ACB',window:'900',scale:'100000',group:'1',speed:'60'}))elements.get(id).value=value;
  const run='a'.repeat(32),at='2026-11-13T03:00:00+00:00';
  const frame={id:'f',index:0,time:at,received_at:at,source_time:at,connection:1,bids:[['20000',100]],asks:[['20050',200]],features:{eligible:false,reason:'UNVERIFIED_UNITS'},clock_delta_ms:0,issues:[],quality:'INVALID',units:'UNVERIFIED_SSI_QUOTE'};
  const trade={id:'b'.repeat(64),time:at,price:'20000',qty:100,side:'BUY',unit_status:'UNVERIFIED_SSI_TRADE'};
  const calls=[];
  const sandbox={console,Intl,Date,Number,Math,JSON,Map,Set,URLSearchParams,performance,Option:class {constructor(text,value){this.text=text;this.value=value;}},window:{devicePixelRatio:1},
    document:{getElementById:id=>{assert.ok(elements.has(id),id);return elements.get(id);},createElement:()=>new Element(),querySelectorAll:()=>[]},
    ResizeObserver:class{observe(){}},requestAnimationFrame(){},setTimeout(){},
    fetch:async(path,options)=>{calls.push([path,options]);let data;
      if(path==='/api/live/status')data={state:'STOPPED',active:false,run_id:run,csrf:'fixture',quotes:1,trades:1,gaps:0,quote_age_seconds:1,persisted_records:2,pending_records:0,rejected:0,connection:1};
      else if(path==='/api/live/runs')data=[{run_id:run,day:'2026-11-13',created_at:at,state:'STOPPED'}];
      else if(path.startsWith('/api/live/view'))data={run_id:run,symbol:'ACB',frames:[frame],trades:[trade],events:[],reset:true,total:1,unit_status:'UNVERIFIED',price_unit:'SSI_SOURCE_PRICE',volume_unit:'SSI_SOURCE_QUANTITY'};
      else if(path.startsWith('/api/live/events'))data=[];else throw Error(path);
      return {ok:true,json:async()=>data};}};
  vm.createContext(sandbox);
  for(const file of ['model.js','heatmap.js','live-model.js','live.js'])vm.runInContext(fs.readFileSync('web/monitor/'+file,'utf8'),sandbox,{filename:file});
  for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));
  assert.ok(elements.get('error').hidden,elements.get('error').textContent);
  assert.ok(rectangles>0,'the heatmap renderer must run');assert.ok(arcs>0,'a captured trade must draw a bubble');
  assert.ok(elements.get('book').children.length);elements.get('pause').onclick();
  for(let i=0;i<2;i++)await new Promise(resolve=>setImmediate(resolve));
  assert.match(elements.get('viewMode').textContent,/xem lại/);
  assert.equal(calls.filter(([p])=>p==='/api/live/stop').length,0);
});
