const test=require('node:test'),assert=require('node:assert/strict');
const B=require('../../web/monitor/breakout.js');

function fixture(api=async()=>packet()){
  class Element{
    constructor(){this.value='';this.hidden=false;this.children=[];this.events={};this.open=true;}
    append(v){this.children.push(v);}replaceChildren(...v){this.children=v;}
    addEventListener(k,f){this.events[k]=f;}getBoundingClientRect(){return {width:800};}
    getContext(){return new Proxy({},{get:()=>()=>{}});}
  }
  const elements=new Map(),document={getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement:()=>new Element()};
  let context={capture:false,data:{dataset_id:'a'.repeat(64),day:'2026-11-11'},cutoff:'2026-11-11T03:15:00Z'};
  const seeks=[],ui=B.create({document,api,context:()=>context,onEnd:()=>{context={...context,cutoff:'2026-11-11T07:00:00Z'};},onSeek:t=>seeks.push(t)});
  ui.sync();return {ui,e:id=>document.getElementById(id),set:c=>{context={...context,...c};},seeks};
}
function packet(){return {state:'READY',symbol:'ACB',day:'2026-11-11',cutoff:'2026-11-11T03:15:00Z',reference:{price:'100',day:'2026-11-10',kind:'STORED_TRADE_HIGH',notice:'not official'},quality:{trades_to_cutoff:10},chart:[],groups:[],episodes:[]};}

test('endpoint descriptions distinguish pending, partial and observed outcome',()=>{
  assert.match(B.outcomeLabel({state:'PENDING'}),/Chưa tới mốc/);
  assert.match(B.outcomeLabel({state:'SESSION_BOUNDARY'}),/nghỉ trưa/);
  assert.match(B.outcomeLabel({state:'PARTIAL_WINDOW',price:'99',above_reference:false,return_bps:-100}),/mẫu một phần/);
  assert.match(B.outcomeLabel({state:'OBSERVED_ENDPOINT',price:'101',above_reference:true,return_bps:100}),/trên mức/);
});
test('reference input travels in an encoded read-only query and full-data study is explicit',async()=>{
  const calls=[],f=fixture(async url=>{calls.push(url);return packet();});
  f.e('breakoutPrice').value='100';f.e('breakoutDay').value='2026-11-10';await f.ui.run();
  assert.match(calls[0],/reference_price=100/);assert.match(calls[0],/reference_day=2026-11-10/);
  assert.equal(f.e('breakoutResult').hidden,false);assert.match(f.e('breakoutReference').textContent,/not official/);
  await f.ui.run(true);assert.match(calls[1],/cutoff=2026-11-11T07%3A00%3A00Z/);
});
test('rewinding or switching dataset hides outcome tables and JSON export',async()=>{
  const f=fixture();await f.ui.run();assert.equal(f.e('breakoutExport').hidden,false);
  f.set({cutoff:'2026-11-11T03:00:00Z'});f.ui.sync();
  assert.equal(f.e('breakoutResult').hidden,true);assert.equal(f.e('breakoutExport').hidden,true);
  f.e('breakoutPrice').value='100';f.set({data:{dataset_id:'b'.repeat(64),day:'2026-11-12'}});f.ui.sync();
  assert.equal(f.e('breakoutPrice').value,'');
});
test('an old response cannot restore future outcomes after a rewind',async()=>{
  let resolve;const f=fixture(()=>new Promise(r=>resolve=r)),pending=f.ui.run();
  f.set({cutoff:'2026-11-11T03:00:00Z'});f.ui.sync();resolve(packet());await pending;
  assert.equal(f.e('breakoutResult').hidden,true);
});
test('capture mode does not splice providers or request a history study',async()=>{
  let calls=0;const f=fixture(async()=>{calls++;return packet();});f.set({capture:true});f.ui.sync();await f.ui.run();
  assert.equal(calls,0);assert.equal(f.e('breakoutRun').disabled,true);assert.match(f.e('breakoutStatus').textContent,/Chuyển sang Lịch sử/);
});
test('changing a reference invalidates the existing result',async()=>{
  const f=fixture();await f.ui.run();f.e('breakoutPrice').events.input();
  assert.equal(f.e('breakoutResult').hidden,true);assert.equal(f.e('breakoutExport').hidden,true);
});
