const test=require('node:test'),assert=require('node:assert/strict');
global.EventReplay=require('../../web/monitor/model.js');
global.SSILiveModel=require('../../web/monitor/live-model.js');
const {Replay}=require('../../web/monitor/capture-model.js');
const time=i=>`2026-11-20T06:00:0${i}Z`;
const frame=i=>({index:100+i,time:time(i),received_at:time(i),source_time:`2026-11-20T05:59:0${i}Z`,connection:1,
  bids:[['20000',100]],asks:[['20050',200]],features:{eligible:false},units:'UNVERIFIED_SSI_QUOTE'});
const packet=()=>({run_id:'a'.repeat(32),dataset_id:'b'.repeat(64),symbol:'FPT',day:'2026-11-20',total:102,
  price_unit:'SSI_SOURCE_PRICE',volume_unit:'SSI_SOURCE_QUANTITY',unit_status:'UNVERIFIED',frames:[frame(0),frame(1)],trades:[],events:[],reset:true});
const event=()=>({id:'c'.repeat(64),type:'SPREAD_WIDENING',detected_index:101,detected_at:'2026-11-20T05:59:01Z',
  detected_received_at:time(1),observed_at:time(1),peak_spread_bps:'10',state:'ACTIVE'});

test('shared capture view preserves original units and both clocks, never certifies unverified frames',()=>{
  const r=new Replay(),d=r.accept(packet());assert.equal(d.provider,'ssi');assert.equal(d.price_unit,'SSI_SOURCE_PRICE');
  assert.equal(d.frames[1].time,time(1));assert.equal(d.frames[1].source_time,'2026-11-20T05:59:01Z');
  assert.equal(d.frames[1].features.eligible,false);assert.equal(d.frames[1].units,'UNVERIFIED_SSI_QUOTE');
  assert.throws(()=>r.accept({...packet(),dataset_id:'invalid'}));
});
test('event positioning uses receive clock and global capture indices map to loaded window only',()=>{
  const r=new Replay();r.accept(packet());r.setEvents([event()],time(1));
  assert.equal(r.eventsAt(0).length,0);const e=r.eventsAt(1)[0];assert.equal(e.detected_index,1);assert.equal(e.capture_index,101);
  assert.equal(e.detected_at,time(1));assert.equal(e.capture_event.detected_at,'2026-11-20T05:59:01Z');
  r.setEvents([{...event(),observed_at:time(2),peak_spread_bps:'999'}],time(1));assert.equal(r.eventsAt(1).length,0);
});
test('capture label does not call stale, paused, other-symbol or archived data realtime',()=>{
  const r=new Replay();r.accept(packet());const now=EventReplay.time(time(2));
  const s={run_id:'a'.repeat(32),active:true,stale:false,quote_ages:{FPT:1}};
  assert.equal(r.label(s,1,true,now),'ĐANG CẬP NHẬT');
  assert.notEqual(r.label({...s,quote_ages:{FPT:99}},1,true,now),'ĐANG CẬP NHẬT');
  assert.notEqual(r.label({...s,run_id:'d'.repeat(32)},1,true,now),'ĐANG CẬP NHẬT');
  assert.notEqual(r.label({...s,stale:true},1,true,now),'ĐANG CẬP NHẬT');
  assert.notEqual(r.label(s,1,false,now),'ĐANG CẬP NHẬT');
});
