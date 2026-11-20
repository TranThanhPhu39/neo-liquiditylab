const test=require('node:test'),assert=require('node:assert/strict');
const {create}=require('../../web/monitor/http.js');
const response=(status,body)=>({status,ok:status===200,json:async()=>body,headers:{get:()=>null}});
test('restoring and busy GETs wait automatically and clear the progress notice on success',async()=>{
  const replies=[response(503,{error:'DATA_RESTORING_RETRY'}),response(503,{error:'SERVER_BUSY_RETRY'}),response(200,{rows:3})],notices=[],delays=[];
  const client=create({fetch:async()=>replies.shift(),wait:async ms=>delays.push(ms),onWaiting:s=>notices.push(s?.code??null)});
  assert.deepEqual(await client.request('/api/library'),{rows:3});
  assert.deepEqual(notices,['DATA_RESTORING_RETRY','SERVER_BUSY_RETRY',null]);assert.ok(delays.every(d=>d>0));
});
test('duplicate library requests share one in-flight request and response',async()=>{
  let calls=0,complete;const client=create({fetch:async()=>{calls++;return new Promise(r=>complete=r);}});
  const a=client.request('/api/library'),b=client.request('/api/library');assert.equal(calls,1);
  complete(response(200,[{id:'event'}]));assert.deepEqual(await a,await b);
});
test('POST is never replayed when storage fails; preserve a distinct storage message',async()=>{
  let calls=0,waits=0;const client=create({fetch:async()=>{calls++;return response(503,{error:'PERSISTENT_STORAGE_UNAVAILABLE'});},wait:async()=>waits++});
  await assert.rejects(client.request('/api/reviews',{method:'POST'}),e=>e.code==='PERSISTENT_STORAGE_UNAVAILABLE'&&e.message.includes('ghi chú'));
  assert.equal(calls,1);assert.equal(waits,0);
});
test('gateway retries and notice lifetime are bounded, HTML error pages are not displayed',async()=>{
  let calls=0;const notices=[];
  const client=create({fetch:async()=>{calls++;return {status:502,ok:false,json:async()=>{throw Error('<html>private diagnostic</html>');}};},wait:async()=>{},onWaiting:s=>notices.push(s)});
  await assert.rejects(client.request('/api/library'),e=>e.code==='GATEWAY_UNAVAILABLE'&&!e.message.includes('private diagnostic'));
  assert.equal(calls,9);assert.equal(notices.at(-1),null);
});
test('authentication failure is not retried as a temporary server failure',async()=>{
  let waits=0;const client=create({fetch:async()=>response(401,{}),wait:async()=>waits++});
  await assert.rejects(client.request('/api/review-history'),e=>e.status===401);assert.equal(waits,0);
});
