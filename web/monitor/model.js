'use strict';
const EventReplay = (() => {
  const FORMAT = 'liquiditylab-event-replay-v1';
  const time = s => {
    const m=typeof s==='string'&&s.match(/^(.*T\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(Z|[+-]\d\d:\d\d)$/);
    if (!m || !Number.isFinite(Date.parse(m[1]+m[3]))) throw Error('Timestamp thiếu múi giờ hoặc không hợp lệ.');
    // Fractional milliseconds keep source microseconds distinct for seek/order.
    return Date.parse(m[1]+m[3])+Number((m[2]||'').padEnd(6,'0'))/1000;
  };
  const numeric = x => x === null || x === undefined || typeof x === 'boolean' || String(x).trim() === '' || !Number.isFinite(Number(x)) ? null : Number(x);
  function validate(d) {
    if (!d || d.format !== FORMAT || d.provider !== 'dnse' || !/^[A-Z][A-Z0-9]{1,11}$/.test(d.symbol) || !['HOSE','HNX','UPCOM'].includes(d.exchange||'HOSE') || d.levels !== 3 || d.price_unit !== 'VND' || d.volume_unit !== 'shares' || !/^[a-f0-9]{64}$/.test(d.dataset_id) || !Array.isArray(d.frames) || !d.frames.length || d.frames.length > (d.detector_scope==='OBSERVATION_ONLY_NO_NEW_UNIT_VERIFICATION'?100000:50000) || !Array.isArray(d.event_patches)) throw Error('Bộ dữ liệu không đúng hợp đồng replay DNSE.');
    if(d.detector_scope==='OBSERVATION_ONLY_NO_NEW_UNIT_VERIFICATION'&&(d.event_patches.length||d.frames.some(f=>f.features?.eligible)))throw Error('Phạm vi chỉ quan sát không được mở detector.');
    let previous = -Infinity;
    for (const f of d.frames) {
      const t = time(f.time); time(f.received_at);
      if (t < previous || !f.features || !['bids', 'asks'].every(s => Array.isArray(f[s]) && f[s].length === 3 && f[s].every(p => Array.isArray(p) && p.length === 2 && (p[0] === null || numeric(p[0]) > 0) && (p[1] === null || (Number.isSafeInteger(p[1]) && p[1] >= 0))))) throw Error('Quan sát sai thứ tự hoặc mức giá/khối lượng không hợp lệ.');
      previous = t;
    }
    previous = -1;
    for (const p of d.event_patches) {
      if (!Number.isInteger(p.at_index) || p.at_index < previous || p.at_index >= d.frames.length || !p.event || !/^[a-f0-9]{64}$/.test(p.event.id) || !Number.isInteger(p.event.detected_index) || !Number.isInteger(p.event.start_index) || p.event.start_index<0 || p.event.detected_index > p.at_index || p.event.start_index > p.event.detected_index || time(p.event.detected_at) > time(d.frames[p.at_index].time)) throw Error('Bằng chứng sự kiện không hợp lệ.');
      const frame=d.frames[p.event.detected_index];
      if(!['EMPIRICALLY_VERIFIED','OFFICIALLY_VERIFIED'].includes(frame.units)&&frame.units!==undefined)throw Error('Sự kiện trên phạm vi chưa xác minh đơn vị.');
      if(p.event.ended_at&&time(p.event.ended_at)>time(d.frames[p.at_index].time))throw Error('Sự kiện chứa kết thúc ở tương lai.');
      previous = p.at_index;
    }
    return d;
  }
  const eventCache=new WeakMap();
  function eventsAt(data, index) {
    let c=eventCache.get(data);
    if(!c||index<c.index){c={index:-1,offset:0,events:new Map()};eventCache.set(data,c);}
    while(c.offset<data.event_patches.length&&data.event_patches[c.offset].at_index<=index){const p=data.event_patches[c.offset++];c.events.set(p.event.id,{...p.event});}
    c.index=index;return [...c.events.values()].map(e=>({...e})).sort((a,b)=>b.detected_index-a.detected_index);
  }
  function windowAt(data, index, seconds = 900) {
    index = Math.max(0, Math.min(data.frames.length - 1, index));
    const stop = time(data.frames[index].time), start = stop - seconds * 1000;
    let lo = 0, hi = index;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (time(data.frames[mid].time) < start) lo = mid + 1; else hi = mid; }
    return {start, stop, frames: data.frames.slice(lo, index + 1), startIndex: lo};
  }
  function geometry(data, index, seconds = 900, side = 'both') {
    const window = windowAt(data, index, seconds), cells = [], bid = [], ask = [], gaps = [];
    let previous = null;
    for (const f of window.frames) {
      const t = time(f.time);
      if (previous !== null && t - previous > data.config.max_observation_gap_seconds * 1000) gaps.push([previous, t]);
      previous = t;
      for (const s of ['bids', 'asks']) {
        if (side !== 'both' && s !== side) continue;
        for (const [price, qty] of f[s]) if (numeric(price) !== null && numeric(qty) !== null) cells.push({t, price: Number(price), qty: Number(qty), side: s});
      }
      bid.push([t,numeric(f.bids[0][0])]);
      ask.push([t,numeric(f.asks[0][0])]);
    }
    return {...window, cells, bid, ask, gaps, maxGapMs:data.config.max_observation_gap_seconds*1000, events: eventsAt(data, index)};
  }
  function indexAt(data, target) {
    let lo = 0, hi = data.frames.length - 1;
    if (target < time(data.frames[0].time)) return 0;
    while (lo < hi) { const mid = Math.ceil((lo + hi) / 2); if (time(data.frames[mid].time) <= target) lo = mid; else hi = mid - 1; }
    return lo;
  }
  function qualityAt(data,index){
    let eligible=0,gaps=0;const reasons={};
    for(let i=0;i<=index;i++){const f=data.frames[i];if(f.features.eligible)eligible++;else {const r=f.features.reason||'UNKNOWN';reasons[r]=(reasons[r]||0)+1;}
      if(i&&f.session===data.frames[i-1].session&&f.features.eligible&&time(f.time)-time(data.frames[i-1].time)>data.config.max_observation_gap_seconds*1000)gaps++;}
    return {observations:index+1,eligible,blocked:index+1-eligible,gaps,reasons};
  }
  function priceComparison(before,after,side){
    const a=new Map(before[side].filter(p=>numeric(p[0])!==null).map(p=>[Number(p[0]),p[1]]));
    const b=new Map(after[side].filter(p=>numeric(p[0])!==null).map(p=>[Number(p[0]),p[1]]));
    return [...new Set([...a.keys(),...b.keys()])].sort((x,y)=>y-x).map(price=>({price,before:a.has(price)?a.get(price):null,after:b.has(price)?b.get(price):null,
      status:!a.has(price)?'NEWLY_OBSERVED':!b.has(price)?'OUT_OF_OBSERVATION':'COMMON'}));
  }
  function filterEvents(events,{type='all',state='all',review='all',notes=[],search=''}={}){
    const latest=new Map(notes.map(r=>[r.event_id,r]));
    return events.filter(e=>(type==='all'||e.type===type)&&(state==='all'||e.state===state)
      &&(review==='all'||(latest.get(e.id)?.verdict||'unreviewed')===review)
      &&(!search||[e.type,e.id,e.detected_at,latest.get(e.id)?.note||''].join(' ').toLowerCase().includes(search.toLowerCase())));
  }
  function validateTrades(p,data){
    if(!p||p.symbol!==data.symbol||p.day!==data.day||!Array.isArray(p.trades))throw Error('Giao dịch khác ngày hoặc mã sổ lệnh.');
    if(p.state==='NOT_COLLECTED'&&!p.trades.length)return p;
    if(p.format!=='liquiditylab-market-trades-v1'||p.provider!=='dnse'||p.board!=='G1'||p.market!=='STO'||p.price_unit!=='VND'||p.volume_unit!=='shares'||!p.quality||typeof p.quality.volume_reconciliation!=='string'||!['EMPIRICALLY_VERIFIED_FOR_ACB_TRADES','CUMULATIVE_SCALE_AND_MATCH_INCREMENT_RECONCILED','ASSUMED_SHARED_SCHEMA'].includes(p.unit_status)||p.trades.length>200000||!/^[a-f0-9]{64}$/.test(p.trade_dataset_id))throw Error('Tập giao dịch không đúng hợp đồng.');
    let previous=-Infinity;const ids=new Set();
    for(const t of p.trades){const at=time(t.time);time(t.received_at);
      if(at<previous||new Date(at+7*3600000).toISOString().slice(0,10)!==data.day||numeric(t.price)===null||numeric(t.price)<=0||!Number.isSafeInteger(t.qty)||t.qty<=0||!Number.isSafeInteger(t.cumulative)||t.cumulative<t.qty||!['BUY','SELL','UNSPECIFIED'].includes(t.side)||!/^[a-f0-9]{64}$/.test(t.id)||ids.has(t.id))throw Error('Giao dịch khớp không hợp lệ.');
      ids.add(t.id);previous=at;
    }return p;
  }
  function bubblesAt(pack,start,stop,{seconds=1,minQty=0}={}){
    if(!pack||!pack.trades?.length)return [];
    if(![1,5,10].includes(seconds)||!Number.isFinite(minQty)||minQty<0)throw Error('Cấu hình bubbles không hợp lệ.');
    const trades=pack.trades,groups=new Map();let lo=0,hi=trades.length;
    while(lo<hi){const mid=(lo+hi)>>1;if(time(trades[mid].time)<start)lo=mid+1;else hi=mid;}
    for(let i=lo;i<trades.length;i++){const t=trades[i],at=time(t.time);if(at>stop)break;
      const key=Math.floor(at/(seconds*1000))+'|'+Number(t.price)+'|'+t.side;
      let b=groups.get(key);if(!b){b={t:at,first:at,last:at,price:Number(t.price),qty:0,count:0,side:t.side};groups.set(key,b);}
      b.qty+=t.qty;b.count++;b.t=b.last=at;
    }return [...groups.values()].filter(b=>b.qty>=minQty).sort((a,b)=>a.t-b.t);
  }
  return {validate, eventsAt, windowAt, geometry, indexAt, time, numeric,qualityAt,priceComparison,filterEvents,validateTrades,bubblesAt};
})();
if (typeof module !== 'undefined') module.exports = EventReplay;
