const {test}=require('node:test');const assert=require('node:assert/strict');
const M=require('../../web/monitor/model.js');
function data(){
  const t=Date.parse('2026-11-11T02:15:00Z');
  const frames=Array.from({length:8},(_,i)=>({time:new Date(t+i*1000).toISOString(),received_at:'2026-11-11T10:00:00Z',bids:[['21000',0],['20950',null],['20900',100]],asks:[['21050',200],['21100',300],['21150',400]],features:{}}));
  for(const f of frames)f.features={spread_bps:'20',bid_depth:100,ask_depth:900,detector_state:'NORMAL',eligible:true};
  const e={id:'b'.repeat(64),type:'SPREAD_WIDENING',start_index:2,started_at:frames[2].time,detected_index:4,detected_at:frames[4].time,state:'ACTIVE',peak_spread_bps:'20',ended_at:null,threshold_bps:'10',baseline:{median_bps:'5',sample_seconds:60,last_source_time:frames[1].time}};
  return {format:'liquiditylab-event-replay-v1',dataset_id:'a'.repeat(64),provider:'dnse',symbol:'ACB',day:'2026-11-11',detector_version:'fixture',levels:3,price_unit:'VND',volume_unit:'shares',frames,config:{max_observation_gap_seconds:30},event_patches:[{at_index:4,event:e},{at_index:7,event:{...e,state:'ENDED',peak_spread_bps:'40',ended_at:frames[7].time}}]};
}
function tradeData(d=data()){
  let cumulative=0;return {format:'liquiditylab-market-trades-v1',provider:'dnse',symbol:d.symbol,day:d.day,board:'G1',market:'STO',price_unit:'VND',volume_unit:'shares',trade_dataset_id:'d'.repeat(64),unit_status:'EMPIRICALLY_VERIFIED_FOR_ACB_TRADES',quality:{volume_reconciliation:'RECONCILED_TO_LAST_REPORTED_CUMULATIVE'},trades:[1.1,1.5,2,4.25,6.5].map((s,i)=>({id:i.toString(16).padStart(64,'0'),time:new Date(M.time(d.frames[0].time)+s*1000).toISOString(),received_at:d.frames[0].received_at,price:i===4?'21500':'21050',qty:100*(i+1),side:i===3?'SELL':i===4?'UNSPECIFIED':'BUY',cumulative:cumulative+=100*(i+1)}))};
}
test('relative episodes are allowed without certifying units, but never absolute-size detectors',()=>{
  const d=data();d.symbol='VNM';d.detector_scope='RELATIVE_RATIOS_ONLY_UNVERIFIED_UNITS';
  d.frames.forEach(f=>{f.units='ASSUMED_SHARED_SCHEMA';f.features.eligible=false;f.features.relative_eligible=true;f.bids[0][1]=100;f.bids[1][1]=100;});
  d.event_patches.forEach(p=>{p.event.analysis_basis=d.detector_scope;p.event.units_certified=false;});
  assert.equal(M.validate(d).symbol,'VNM');assert.deepEqual(M.eventsAt(d,3),[]);
  assert.equal(M.eventsAt(d,4).length,1);assert.equal(M.qualityAt(d,4).eligible,0);assert.equal(M.qualityAt(d,4).relativeEligible,5);
  const bad=structuredClone(d);bad.event_patches[0].event.type='IMBALANCE_PERSISTENCE';assert.throws(()=>M.validate(bad));
  const certified=structuredClone(d);certified.frames[0].features.eligible=true;assert.throws(()=>M.validate(certified));
  const forged=structuredClone(d);forged.event_patches[0].event.units_certified=true;assert.throws(()=>M.validate(forged));
});
test('market observation replay accepts other exchanges and never grants detector permission',()=>{
  const d=data();d.symbol='SHS';d.exchange='HNX';d.event_patches=[];d.detector_scope='OBSERVATION_ONLY_NO_NEW_UNIT_VERIFICATION';
  d.frames.forEach(f=>{f.features.eligible=false;f.units='ASSUMED_SHARED_SCHEMA';});
  assert.equal(M.validate(d).symbol,'SHS');
  d.frames[0].features.eligible=true;assert.throws(()=>M.validate(d));
  d.frames[0].features.eligible=false;d.symbol='../.env';assert.throws(()=>M.validate(d));
});
test('replay hides future events, endpoints and peak values',()=>{
  const d=M.validate(data());assert.deepEqual(M.eventsAt(d,3),[]);
  assert.equal(M.eventsAt(d,4)[0].ended_at,null);assert.equal(M.eventsAt(d,4)[0].peak_spread_bps,'20');
  assert.equal(M.eventsAt(d,7)[0].state,'ENDED');assert.equal(M.eventsAt(d,4)[0].state,'ACTIVE');
});
test('heatmap geometry uses only prefix and preserves zero versus unknown',()=>{
  const d=data();d.frames[7].bids[0]=['99999',999999];const g=M.geometry(d,4,900);
  assert.ok(g.cells.every(c=>c.t<=M.time(d.frames[4].time)));assert.ok(!g.cells.some(c=>c.price===99999));
  assert.ok(g.cells.some(c=>c.qty===0));assert.ok(!g.cells.some(c=>c.price===20950));
});
test('time seek uses last available prefix and validates sequence/naive timestamps',()=>{
  const d=data();assert.equal(M.indexAt(d,M.time(d.frames[4].time)+999),4);
  d.frames[4].time=d.frames[1].time;assert.throws(()=>M.validate(d));assert.throws(()=>M.time('2026-11-11T09:00:00'));
});
test('gap is retained and malformed quotes or future patches rejected',()=>{
  const d=data();d.frames[7].time='2026-11-11T02:16:00Z';assert.equal(M.geometry(d,7).gaps.length,1);
  const bad=data();bad.frames[1].bids[0][1]=true;assert.throws(()=>M.validate(bad));
  const future=data();future.event_patches[0].event.detected_index=5;assert.throws(()=>M.validate(future));
});
test('source microseconds remain ordered instead of collapsing to milliseconds',()=>{
  const a=M.time('2026-11-11T09:15:00.000001+07:00'),b=M.time('2026-11-11T09:15:00.000002+07:00');
  assert.ok(a<b);
});

test('absolute price evidence preserves outside levels, unknown quantities and true zero',()=>{
  const d=data(),a=d.frames[0],b=structuredClone(a);b.bids=[['21050',123],['21000',0],['20950',null]];
  const rows=M.priceComparison(a,b,'bids');
  assert.deepEqual(rows.find(r=>r.price===20900),{price:20900,before:100,after:null,status:'OUT_OF_OBSERVATION'});
  assert.equal(rows.find(r=>r.price===21050).status,'NEWLY_OBSERVED');
  assert.equal(rows.find(r=>r.price===21000).after,0);assert.equal(rows.find(r=>r.price===20950).after,null);
  b.bids[0]=[null,null];d.frames[1]=b;
  assert.equal(M.geometry(d,1).bid[1][1],null);
});

test('prefix quality, latest verdict filters and unit guard do not invent normality',()=>{
  const d=data();d.frames[6].features={...d.frames[6].features,eligible:false,reason:'UNVERIFIED_UNITS'};
  assert.equal(M.qualityAt(d,5).blocked,0);assert.equal(M.qualityAt(d,6).blocked,1);
  const events=M.eventsAt(d,7),e=events[0],notes=[{event_id:e.id,verdict:'not_useful',note:'old'},{event_id:e.id,verdict:'useful',note:'check book'}];
  assert.equal(M.filterEvents(events,{review:'useful',search:'check',notes}).length,1);
  assert.equal(M.filterEvents(events,{review:'not_useful',notes}).length,0);
  d.symbol='FPT';d.frames[4].units='ASSUMED_SHARED_SCHEMA';assert.throws(()=>M.validate(d),/đơn vị/);
});

test('trade bubbles aggregate true executions at same price/side using only known prefix',()=>{
  const d=data(),p=M.validateTrades(tradeData(d),d),start=M.time(d.frames[0].time);
  let b=M.bubblesAt(p,start,start+1200);assert.equal(b.length,1);assert.equal(b[0].qty,100);assert.equal(b[0].t,start+1100);
  b=M.bubblesAt(p,start,start+1600);assert.equal(b[0].qty,300);assert.equal(b[0].count,2);
  assert.equal(M.bubblesAt(p,start,start+1200)[0].qty,100); // rewind does not keep future aggregate
  b=M.bubblesAt(p,start,start+7000,{seconds:5,minQty:0});assert.equal(b.length,3);assert.equal(b[0].qty,600);assert.equal(b[1].side,'SELL');assert.equal(b[2].side,'UNSPECIFIED');
  assert.equal(M.bubblesAt(p,start,start+1600,{seconds:1,minQty:400}).length,0);
  assert.equal(M.bubblesAt(p,start+1600,start+3000)[0].qty,300); // no volume outside view
});

test('trade package rejects cross-symbol/day, duplicate identity, bad quantities and time order',()=>{
  for(const mutate of [p=>p.symbol='FPT',p=>p.day='2026-11-10',p=>p.trades[0].qty=true,p=>p.trades[1].id=p.trades[0].id,p=>p.trades[1].time=p.trades[0].time.replace('01.100','00.100')]){const p=tradeData();mutate(p);assert.throws(()=>M.validateTrades(p,data()));}
  assert.deepEqual(M.bubblesAt(null,0,1),[]);assert.deepEqual(M.bubblesAt({trades:[]},0,1),[]);
});
for(const publicRead of [false,true])test('app loads history and evidence in '+(publicRead?'anonymous read-only mode':'owner mode'),async()=>{
  const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
  const elements=new Map(),raf=[],timers=new Map(),requests=[];let notes=[],saved=null,draws=0,analyzed=null,arcs=0,timerId=0,holdCapture=false,heldCapture=null;
  class Element{
    constructor(id=''){this.id=id;this.value='';this.dataset={};this.nodes=[];this.listeners={};this.hidden=false;this.disabled=false;this.parentElement=this;this.classList={toggle(){}};}
    set textContent(v){this.text=String(v);this.nodes=[];}get textContent(){return (this.text||'')+this.nodes.map(n=>n.textContent||'').join(' ');}
    append(...nodes){this.nodes.push(...nodes);}replaceChildren(...nodes){this.text='';this.nodes=nodes;}
    addEventListener(type,fn){this.listeners[type]=fn;}setAttribute(){}showModal(){this.open=true;}close(){this.open=false;}
    getBoundingClientRect(){return {width:850,height:385,left:0,top:0};}
    getContext(){const functions=['scale','fillRect','save','beginPath','rect','clip','stroke','moveTo','lineTo','strokeRect','setLineDash','fill','restore','fillText','arc'];const ctx={};for(const name of functions)ctx[name]=(...args)=>{if(name==='fillRect'){draws++;assert.ok(args.every(Number.isFinite));}if(name==='arc'){arcs++;assert.ok(args.every(Number.isFinite));assert.ok(args[2]>=2&&args[2]<=26);}};return ctx;}
    async emit(type,event={}){return this.listeners[type]?.(event);}
  }
  const get=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);};
  get('window').value='900';get('scale').value='250000';get('speed').value='60';get('error').hidden=true;
  get('showBubbles').checked=true;get('bubbleGroup').value='1';get('bubbleMin').value='0';get('bubbleScale').value='10000';
  const nav=['watch','analysis','review'].map(page=>{const e=new Element();e.dataset.page=page;return e;});
  const d=data(),fpt=structuredClone(d);fpt.symbol='FPT';fpt.dataset_id='f'.repeat(64);fpt.event_patches=[];
  for(const f of fpt.frames){f.units='ASSUMED_SHARED_SCHEMA';f.features.eligible=false;f.features.reason='UNVERIFIED_UNITS';f.features.detector_state='INHIBITED';}
  const packs=new Map([[d.dataset_id,d],[fpt.dataset_id,fpt]]);
  const entry=p=>({dataset_id:p.dataset_id,symbol:p.symbol,day:p.day,observations:p.frames.length,event_counts:{SPREAD_WIDENING:p.event_patches.length?1:0}});
  let catalog=[entry(d),entry(fpt)];
  const context=vm.createContext({document:{getElementById:get,createElement:()=>new Element(),querySelectorAll:q=>q==='[data-page]'?nav:[]},window:{devicePixelRatio:1},ResizeObserver:class{observe(){}},requestAnimationFrame:fn=>raf.push(fn),performance:{now:()=>0},Intl,Date,console,URLSearchParams,
    setTimeout:(fn,ms)=>{const id=++timerId;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),
    fetch:async(url,options={})=>{let result;
      requests.push({url,method:options.method||'GET'});
      if(url==='/api/datasets')result={application_version:'offline-monitor-v7-public',csrf:publicRead?null:'fixture',read_only:publicRead,datasets:catalog};
      else if(url==='/api/live/status')result={read_only:true,csrf:null,run_id:'9'.repeat(32),active:true,quote_counts:{ACB:108,FPT:108},quote_ages:{ACB:0,FPT:0},gaps:2,stale:false};
      else if(url==='/api/live/runs')result=[{run_id:'9'.repeat(32),day:d.day,created_at:d.frames[0].time,symbols:['ACB','FPT'],quote_counts:{ACB:108,FPT:108}}];
      else if(url.startsWith('/api/live/view?')){const symbol=new URLSearchParams(url.split('?')[1]).get('symbol');result={run_id:'9'.repeat(32),dataset_id:'8'.repeat(64),day:d.day,symbol,total:108,frames:d.frames.map((f,i)=>({...structuredClone(f),index:100+i,units:'UNVERIFIED_SSI_QUOTE',connection:1,source_time:f.time,features:{...f.features,eligible:false,reason:'UNVERIFIED_UNITS'}})),trades:[],events:[],reset:true,unit_status:'UNVERIFIED',price_unit:'SSI_SOURCE_PRICE',volume_unit:'SSI_SOURCE_QUANTITY'};}
      else if(url.startsWith('/api/live/events?')){const at=new URLSearchParams(url.split('?')[1]).get('at'),patch=d.event_patches.filter(p=>M.time(d.frames[p.at_index].time)<=M.time(at)).at(-1);result=patch?[{...patch.event,start_index:102,detected_index:104,last_index:100+patch.at_index,observed_at:d.frames[patch.at_index].time,detected_received_at:d.frames[4].time}]:[];}
      else if(url.startsWith('/api/live/reviews?'))result=[];
      else if(url.startsWith('/api/trades?'))result=tradeData(packs.get(url.split('=')[1]));
      else if(url.startsWith('/api/dataset?'))result=packs.get(url.split('=')[1]);
      else if(url.startsWith('/api/watchlist?'))result=[{symbol:'ACB',bid:'21000',ask:'21050',time:d.frames[0].time,status:'VERIFIED',dataset_id:d.dataset_id,features:{bid_depth:100,ask_depth:900}}];
      else if(url==='/api/library')result=[{...entry(d),event:d.event_patches[1].event,review:notes.at(-1),archived:catalog.find(e=>e.dataset_id===d.dataset_id).archived}];
      else if(url==='/api/analyze'){analyzed=JSON.parse(options.body);const p=structuredClone(d);p.dataset_id='c'.repeat(64);packs.set(p.dataset_id,p);catalog=[{...entry(d),archived:true},entry(p),entry(fpt)];result=entry(p);}
      else if(options.method==='POST'){saved=JSON.parse(options.body);assert.equal(options.headers['X-Monitor-CSRF'],'fixture');notes.push({...saved,created_at:'2026-11-12T00:00:00Z'});result={state:'SAVED'};}
      else result=notes.filter(n=>n.dataset_id===url.split('=')[1]);if(holdCapture&&url.startsWith('/api/live/view?'))await new Promise(resolve=>heldCapture=resolve);return {ok:true,json:async()=>result};}});
  for(const file of ['model.js','heatmap.js','live-model.js','capture-model.js','http.js','app.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../../web/monitor',file),'utf8'),context);
  for(let i=0;i<3;i++)await new Promise(r=>setImmediate(r));
  assert.equal(get('error').hidden,true);assert.equal(get('eventCount').textContent,'0');assert.ok(draws>0);
  await get('sessionEnd').emit('click');assert.equal(get('eventCount').textContent,'1');
  assert.ok(arcs>0);assert.match(get('bubbleStatus').textContent,/bubbles/);
  const was=arcs;get('showBubbles').checked=false;await get('showBubbles').emit('change');assert.equal(arcs,was);assert.match(get('bubbleStatus').textContent,/Đã ẩn/);
  get('showBubbles').checked=true;await get('showBubbles').emit('change');assert.ok(arcs>was);
  await get('events').nodes[0].emit('click');assert.equal(get('cursor').value,4);assert.equal(get('reviewForm').hidden,publicRead);
  assert.match(get('evidence').textContent,/20/);assert.doesNotMatch(get('evidence').textContent,/40 bps/);
  if(publicRead){
    assert.equal(get('openConfig').disabled,true);
    await get('reviewForm').emit('submit',{preventDefault(){}});
    await get('configForm').emit('submit',{preventDefault(){}});
    await get('openConfig').emit('click');
    assert.equal(saved,null);assert.equal(analyzed,null);assert.notEqual(get('configDialog').open,true);
    await get('first').emit('click');assert.equal(get('eventCount').textContent,'0');
    await get('play').emit('click');raf.shift()(1000);assert.equal(get('cursor').value,7);
    get('dataMode').value='capture';await get('dataMode').emit('change');
    assert.equal(get('dataMode').value,'capture');assert.equal(get('error').hidden,true,get('error').textContent);
    assert.equal(get('symbolSelect').value,'ACB');assert.match(get('provenance').textContent,/101/);
    assert.match(get('quality').textContent,/SSI/);assert.equal(get('openConfig').disabled,true);
    assert.equal(get('exportCSV').hidden,true);assert.match(get('exportJSON').href,/api\/live\/export/);
    get('cursor').value='3';await get('cursor').emit('input');assert.equal(get('eventCount').textContent,'0');
    async function eventTimers(){for(const [id,t] of [...timers])if(t.ms===250){timers.delete(id);await t.fn();}for(let i=0;i<4;i++)await new Promise(r=>setImmediate(r));}
    await eventTimers();assert.equal(get('eventCount').textContent,'0');
    get('cursor').value='4';await get('cursor').emit('input');await eventTimers();
    assert.equal(get('eventCount').textContent,'1');await get('events').nodes[0].emit('click');await eventTimers();
    assert.match(get('evidence').textContent,/20/);assert.doesNotMatch(get('evidence').textContent,/40 bps/);
    assert.equal(get('reviewForm').hidden,true);
    get('symbolSelect').value='FPT';await get('symbolSelect').emit('change');assert.equal(get('symbolSelect').value,'FPT');
    get('dataMode').value='history';await get('dataMode').emit('change');
    assert.equal(get('dataMode').value,'history');assert.equal(get('symbolSelect').value,'FPT');
    assert.match(get('quality').textContent,/DNSE/);assert.equal(get('captureActions').hidden,true);
    const vnm=structuredClone(fpt);vnm.symbol='VNM';vnm.dataset_id='7'.repeat(64);packs.set(vnm.dataset_id,vnm);catalog.push(entry(vnm));
    get('dataMode').value='history';await get('dataMode').emit('change');get('symbolSelect').value='VNM';await get('symbolSelect').emit('change');
    const count=requests.filter(r=>r.url.startsWith('/api/live/view?')).length;
    get('dataMode').value='capture';await get('dataMode').emit('change');
    assert.equal(get('symbolSelect').value,'VNM');assert.equal(get('play').disabled,true);assert.match(get('book').textContent,/VNM/);
    assert.equal(requests.filter(r=>r.url.startsWith('/api/live/view?')).length,count,'unknown capture symbol must not fall back to ACB');
    get('dataMode').value='history';await get('dataMode').emit('change');get('symbolSelect').value='ACB';await get('symbolSelect').emit('change');
    holdCapture=true;get('dataMode').value='capture';const opening=get('dataMode').emit('change');
    for(let i=0;i<4;i++)await new Promise(r=>setImmediate(r));assert.equal(typeof heldCapture,'function');
    get('dataMode').value='history';await get('dataMode').emit('change');heldCapture();await opening;
    assert.equal(get('dataMode').value,'history');assert.match(get('quality').textContent,/DNSE/);
    assert.equal(get('captureActions').hidden,true,'late capture responses cannot overwrite a history view');
    assert.equal(requests.some(r=>r.method==='POST'),false);
    return;
  }
  get('note').value='fixture review';await get('reviewForm').emit('submit',{preventDefault(){}});assert.equal(saved.note,'fixture review');
  await get('first').emit('click');assert.equal(get('eventCount').textContent,'0');assert.equal(get('reviewForm').hidden,true);
  await get('play').emit('click');raf.shift()(1000);assert.equal(get('cursor').value,7);assert.equal(get('eventCount').textContent,'1');
  get('eventType').value='DEPTH_DROP';await get('eventType').emit('change');assert.equal(get('events').nodes[0].className,'empty-text');
  get('eventType').value='all';await get('eventType').emit('change');
  await nav[0].emit('click');await new Promise(r=>setImmediate(r));assert.equal(get('watchScreen').hidden,false);assert.match(get('watchTable').textContent,/ACB/);
  await nav[2].emit('click');await new Promise(r=>setImmediate(r));assert.equal(get('reviewScreen').hidden,false);assert.match(get('libraryTable').textContent,/fixture review/);
  get('librarySearch').value='no matching note';await get('librarySearch').emit('input');assert.match(get('libraryStatus').textContent,/0 hồ sơ/);
  get('librarySearch').value='';await get('librarySearch').emit('input');
  await get('openConfig').emit('click');assert.equal(get('configDialog').open,true);
  get('cfgMultiplier').value='2';get('cfgIncrease').value='2';get('cfgDepth').value='50';get('cfgImbalance').value='80';get('cfgDuration').value='5';
  await get('configForm').emit('submit',{preventDefault(){}});assert.equal(analyzed.parameters.duration_seconds,5);assert.equal(analyzed.parameters.depth_drop,'0.5');
  assert.equal(catalog.find(e=>e.dataset_id===d.dataset_id).archived,true);assert.equal(notes[0].dataset_id,d.dataset_id);
  get('symbolSelect').value='FPT';await get('symbolSelect').emit('change');assert.equal(get('eventCount').textContent,'0');assert.equal(get('unitWarning').hidden,false);assert.match(get('bookUnits').textContent,/Chưa xác minh/);
});
