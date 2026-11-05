const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=path.resolve(__dirname,'../..');
const MarketView=require('../../web/market-view.js');
const DepthSurface=require('../../web/depth-surface.js');
const EngineClient=require('../../web/engine-client.js');
function row(clock='10:00:00',extra={}){
  const r={symbol:'ACB',exchange:'HOSE',provider:'dnse',price_unit:'VND',volume_unit:'shares',source_timestamp:`2026-11-05T${clock}+07:00`,source_local_time:`2026-11-05T${clock}+07:00`,local_date:'2026-11-05',market_session:'DNSE_CONTINUOUS_AM',data_quality_status:'VALID',unit_verification_status:'EMPIRICALLY_VERIFIED',bid_observed_depth_shares:600,ask_observed_depth_shares:600,financial_eligible:true,event_id:clock};
  for(const s of ['bid','ask'])for(let i=1;i<=3;i++){r[`${s}_price_${i}`]=String(20500+(s==='bid'?-1:1)*i*50);r[`${s}_vol_${i}`]=i*100;}
  return {...r,...extra};
}
const q={day:'2026-11-05',symbol:'ACB',time:'14:20',depth_side:'bid',depth_mode:'volume'};
test('native geometry excludes future/other symbols, keeps source microseconds and real zero',()=>{
  const a=row('10:00:00.000001',{bid_vol_1:0,bid_vol_2:null}),b=row('10:00:00.000002');
  const payload={records:[a,b,row('14:21:00'),row('10:00:00',{symbol:'FPT'})]};
  const result=MarketView.view(payload,q);
  assert.equal(result.depth_surface.points,2);assert.equal(result.record.source_timestamp,b.source_timestamp);
  assert.deepEqual(result.depth_surface.grids.bid.z[0],[0,null,300]);
  assert.deepEqual(MarketView.view(payload,{...q,depth_mode:'cumulative'}).depth_surface.grids.bid.z[0],[0,null,null]);
  assert.equal(result.record.financial_eligible,false);assert.equal(result.record.eligibility_pending,true);
  assert.equal(payload.records[0].financial_eligible,true);
  assert.throws(()=>MarketView.read({records:[row('10:00:00',{source_timestamp:'2026-11-05T10:00:00'})]}));
  assert.throws(()=>MarketView.read({records:[row('10:00:00',{volume_unit:'lots'})]}));
});
test('SVG has real faces, gaps remain holes and one snapshot stays a slice',()=>{
  const records=[row('11:20:00'),row('11:26:00'),row('13:00:00'),row('13:06:00')];
  const m=MarketView.view({records},q).depth_surface,svg=DepthSurface.scene(m);
  assert.equal(m.gaps,1);assert.equal((svg.match(/<polygon /g)||[]).length,4);
  assert.equal((DepthSurface.scene(MarketView.surface([row()],q)).match(/<polygon /g)||[]).length,0);
  assert.match(svg,/<svg /);assert.doesNotMatch(svg,/NaN|Infinity|WebGL|<script/);
  assert.notEqual(svg,DepthSurface.scene(m,{yaw:.2,pitch:.8,zoom:1.2}));
  const empty=MarketView.surface([row('15:00:00')],q);assert.equal(empty.points,0);assert.equal(DepthSurface.scene(empty),'');
});
test('SVG drag, zoom, reset and clear work without Plotly',()=>{
  const events={},element={addEventListener:(name,fn)=>events[name]=fn,setPointerCapture:()=>{}};
  const model=MarketView.surface([row(),row('10:06:00')],q);
  DepthSurface.render(element,model);const original=element.innerHTML;
  events.pointerdown({clientX:0,clientY:0,pointerId:1});events.pointermove({clientX:50,clientY:20});
  assert.notEqual(element.innerHTML,original);events.pointerup();
  DepthSurface.reset(element);assert.equal(element.innerHTML,original);
  events.wheel({deltaY:100,preventDefault:()=>{}});assert.notEqual(element.innerHTML,original);
  events.dblclick();assert.equal(element.innerHTML,original);
  DepthSurface.clear(element,'EMPTY');assert.equal(element.textContent,'EMPTY');
});
test('worker starts lazily and a hung startup rejects with a deadline',async()=>{
  let started=0,terminated=0;
  const c=new EngineClient({startupMs:5,createWorker:()=>{started++;return {terminate:()=>terminated++};}});
  assert.equal(started,0);await assert.rejects(c.call('view','{}'),/Python/);
  assert.equal(started,1);assert.equal(terminated,1);assert.equal(c.pending.size,0);
});
test('worker request timeout and fatal messages reject every caller',async()=>{
  let w;const c=new EngineClient({requestMs:5,createWorker:()=>w={postMessage:()=>{},terminate:()=>{}}});
  const p=c.call('view','{}');w.onmessage({data:{ready:true}});await assert.rejects(p,/Python/);assert.equal(c.pending.size,0);
  let f;const d=new EngineClient({createWorker:()=>f={postMessage:()=>{},terminate:()=>{}}});
  const pending=d.call('view','{}');f.onmessage({data:{fatal:'fixture'}});await assert.rejects(pending,/Python/);
});
function dom(){
  const elements=new Map();
  function element(id){if(elements.has(id))return elements.get(id);const e={id,value:'',textContent:'',innerHTML:'',dataset:{},disabled:false,checked:false,hidden:false,files:[],classList:{toggle:()=>{}},setAttribute:()=>{},addEventListener:()=>{},replaceChildren(...nodes){this.nodes=nodes;},setPointerCapture:()=>{}};elements.set(id,e);return e;}
  const defaults={clock:'14:20',day:q.day,symbol:'ACB',side:'sell',quantity:'150000',reverseQty:'150000',benchmark:'best',fee:'0',tax:'0',shock:'50',threshold:'70',portfolio:'symbol,quantity\nACB,150000',depthSide:'bid',depthMode:'volume'};
  for(const [id,value] of Object.entries(defaults))element(id).value=value;
  return {elements,document:{getElementById:element,querySelectorAll:()=>[],createElement:()=>({})}};
}
test('full app renders cloud and private 3D while Worker is unavailable',async()=>{
  const {elements,document}=dom(),payload={records:[row(),row('10:06:00')],catalog:{ACB:{exchange:'HOSE'}},cutoff:'2026-11-05T15:10:00+07:00'};
  let workerStarts=0;
  class Cloud{constructor(){this.enabled=true;}async initialize(){return this.index={latest_day:q.day,days:[{date:q.day}],generated_at:'2026-11-05T10:00:00Z'};}async market(){return payload;}day(){return {preferred_time:q.time};}async prepare(){return {key:'fixture',payload};}}
  class UnavailableEngine extends EngineClient{constructor(options){super({...options,createWorker:()=>{workerStarts++;throw Error('blocked');}});}}
  const context=vm.createContext({document,LiquidityCloud:Cloud,MarketView,DepthSurface,EngineClient:UnavailableEngine,location:{hash:''},window:{addEventListener:()=>{},scrollTo:()=>{}},console,setTimeout,clearTimeout,Intl,Date,URL,Blob});
  vm.runInContext(fs.readFileSync(path.join(root,'web/app.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(workerStarts,0);assert.match(elements.get('depthSurface').innerHTML,/<svg /);assert.match(elements.get('book').innerHTML,/20450|20,450/);
  await vm.runInContext("state.page='execution';update()",context);assert.equal(workerStarts,1);assert.match(elements.get('message').textContent,/Python/);
  await vm.runInContext("state.page='liquidity';update()",context);assert.match(elements.get('depthSurface').innerHTML,/<svg /);
  await vm.runInContext('demo()',context);assert.equal(workerStarts,1);assert.match(elements.get('depthSummary').textContent,/8 snapshot/);
});
