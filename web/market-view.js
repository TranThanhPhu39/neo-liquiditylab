'use strict';
/* Descriptive charts only. A1/B5 eligibility and money calculations stay in Python. */
const MarketView=(()=>{
  const VN=25200e6;
  function micros(value){
    const m=String(value).match(/^(.*T\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)$/);
    if(!m)throw new Error('Thiếu thời gian nguồn có múi giờ.');
    const result=Date.parse(m[1]+m[3])*1000+Number((m[2]||'').padEnd(6,'0').slice(0,6));
    if(!Number.isSafeInteger(result))throw new Error('Thời gian nguồn không hợp lệ.');
    return result;
  }
  const local=t=>new Date((t+VN)/1000).toISOString();
  function numeric(value,quantity=false){
    if(!['number','string'].includes(typeof value)||!String(value).trim().match(/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i))return null;
    const n=Number(value);
    return Number.isFinite(n)&&(quantity?Number.isSafeInteger(n)&&n>=0:n>0)?n:null;
  }
  const usable=(r,s,i)=>numeric(r[`${s}_price_${i}`])!==null&&numeric(r[`${s}_vol_${i}`],true)!==null;
  function depth(r,s){
    const values=[1,2,3].filter(i=>usable(r,s,i)).map(i=>numeric(r[`${s}_vol_${i}`],true));
    const sum=values.reduce((a,b)=>a+b,0);
    return values.length&&Number.isSafeInteger(sum)?sum:null;
  }
  function read(payload){
    const rows=Array.isArray(payload)?payload:payload?.records||[payload];
    if(!Array.isArray(rows)||!rows.length||rows.length>30000)throw new Error('Cần từ 1 đến 30.000 snapshot DNSE chuẩn hóa.');
    const records=rows.map(r=>{
      if(!r||r.provider!=='dnse'||r.price_unit!=='VND'||r.volume_unit!=='shares')throw new Error('Chỉ nhận schema DNSE chuẩn hóa VND / shares.');
      if(!/^[A-Z0-9_]{1,12}$/.test(r.symbol))throw new Error('Mã chứng khoán không hợp lệ.');
      const t=micros(r.source_timestamp),day=local(t).slice(0,10);
      if(r.local_date&&r.local_date!==day)throw new Error('Ngày dữ liệu không khớp thời gian nguồn.');
      for(const s of ['bid','ask'])for(const i of [1,2,3]){
        for(const [field,quantity] of [[`${s}_price_${i}`,false],[`${s}_vol_${i}`,true]]){
          if(r[field]!==null&&r[field]!==undefined&&numeric(r[field],quantity)===null)throw new Error('Giá hoặc khối lượng chuẩn hóa không hợp lệ.');
        }
      }
      // Never trust an imported financial_eligible flag. Python checks it on A1/B5.
      return {...r,local_date:day,financial_eligible:false,eligibility_pending:true};
    });
    const catalogs={};
    if(payload?.catalog&&typeof payload.catalog==='object'&&!Array.isArray(payload.catalog))catalogs[local(micros(payload.cutoff)).slice(0,10)]=payload.catalog;
    const days=[...new Set(records.map(r=>r.local_date))].sort();
    return {records,catalogs,info:{days,count:records.length,symbols:Object.fromEntries(days.map(d=>[d,[...new Set([...records.filter(r=>r.local_date===d).map(r=>r.symbol),...Object.keys(catalogs[d]||{})])].sort()]))}};
  }
  // Match Python round-to-even when bounding the geometry.
  const round=n=>n%1===.5?(Math.floor(n)%2?Math.ceil(n):Math.floor(n)):Math.round(n);
  function surface(records,q,limit=180){
    const cutoff=micros(q.day+'T'+(q.time||'23:59')+':59.999999+07:00'),unique=new Map();
    for(const r of records){const t=micros(r.source_timestamp);if(r.symbol===q.symbol&&local(t).slice(0,10)===q.day&&t<=cutoff)unique.set(t,r);}
    let rows=[...unique.entries()].sort((a,b)=>a[0]-b[0]);const available=rows.length;
    if(rows.length>limit)rows=Array.from({length:limit},(_,i)=>rows[round(i*(available-1)/(limit-1))]);
    const grids=Object.fromEntries(['bid','ask'].map(s=>[s,{x:[],y:[],z:[],text:[]}]));
    let previous=null,gaps=0;
    for(const [t,r] of rows){
      const seconds=((t+VN)%86400e6)/1e6;
      if(previous!==null&&seconds-previous>900){gaps++;for(const g of Object.values(grids)){g.x.push([null,null,null]);g.y.push(Array(3).fill((seconds+previous)/2));g.z.push([null,null,null]);g.text.push(['','','']);}}
      for(const [s,g] of Object.entries(grids)){
        const prices=[1,2,3].map(i=>numeric(r[`${s}_price_${i}`]));
        let quantities=[1,2,3].map((i,j)=>prices[j]===null?null:numeric(r[`${s}_vol_${i}`],true));
        if(q.depth_mode==='cumulative'){let sum=0;quantities=quantities.map(n=>{sum=n===null||sum===null?null:sum+n;if(!Number.isSafeInteger(sum))sum=null;return sum;});}
        g.x.push(prices);g.y.push([seconds,seconds,seconds]);g.z.push(quantities);g.text.push(Array(3).fill(local(t).slice(11,19)));
      }
      previous=seconds;
    }
    return {symbol:q.symbol,day:q.day,points:rows.length,available_points:available,gaps,grids,side:q.depth_side||'bid',cumulative:q.depth_mode==='cumulative',units_verified:rows.length>0&&rows.every(([,r])=>['EMPIRICALLY_VERIFIED','OFFICIALLY_VERIFIED'].includes(r.unit_verification_status))};
  }
  function view(payload,q){
    const {records,catalogs}=read(payload),cutoff=micros(q.day+'T'+(q.time||'23:59')+':59.999999+07:00');
    const today=records.filter(r=>r.local_date===q.day),selected=today.filter(r=>micros(r.source_timestamp)<=cutoff).sort((a,b)=>micros(a.source_timestamp)-micros(b.source_timestamp));
    const latest=new Map(selected.map(r=>[r.symbol,r])),record=latest.get(q.symbol)||null,catalog=catalogs[q.day]||{};
    const symbols=[...new Set([...today.map(r=>r.symbol),...Object.keys(catalog)])].sort();
    let metrics=null;
    if(record&&record.data_quality_status!=='INVALID'){
      const bid=depth(record,'bid'),ask=depth(record,'ask');
      const p=usable(record,'bid',1)?numeric(record.bid_price_1):null,a=usable(record,'ask',1)?numeric(record.ask_price_1):null;
      const spread=p!==null&&a!==null?a-p:null,mid=spread!==null?(p+a)/2:null;
      metrics={bid_depth:bid,ask_depth:ask,relative_spread:mid?spread/mid:null,obi:bid!==null&&ask!==null&&bid+ask?(bid-ask)/(bid+ask):null};
    }
    const samples=selected.filter(r=>r.symbol===q.symbol),stride=Math.max(1,Math.ceil(samples.length/300)),history=samples.filter((_,i)=>i%stride===0);
    if(samples.length&&history.at(-1)!==samples.at(-1))history.push(samples.at(-1));
    return {record,metrics,rows:selected.length,history,depth_surface:surface(selected,q),watchlist:symbols.map(s=>({...latest.get(s),symbol:s,exchange:latest.get(s)?.exchange||catalog[s]?.exchange,has_snapshot:latest.has(s)}))};
  }
  return {read,view,surface,micros};
})();
if(typeof module!=='undefined')module.exports=MarketView;
