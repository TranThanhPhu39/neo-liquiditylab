'use strict';
/* Same-origin, content-verified, read-only history. No API keys or account data. */
class LiquidityCloud {
  constructor(){this.index=null;this.cache=new Map();this.hydrated=null;this.enabled=true;}
  async initialize(){
    const response=await fetch(new URL('./data/index.json?v='+Date.now(),location.href),{cache:'no-store',credentials:'omit'});
    if(!response.ok)throw new Error('Chưa tải được danh mục dữ liệu web. Hãy bấm Tải lại dữ liệu.');
    const index=await response.json();
    if(index.format!=='liquiditylab-cloud-v1'||!Array.isArray(index.days)||!index.days.length)throw new Error('Danh mục dữ liệu web không hợp lệ.');
    if(index.days.some(d=>!/^\d{4}-\d{2}-\d{2}$/.test(d.date))||!index.days.some(d=>d.date===index.latest_day))throw new Error('Ngày dữ liệu web không hợp lệ.');
    this.index=index;this.hydrated=null;return index;
  }
  day(day){const entry=this.index?.days.find(d=>d.date===day);if(!entry)throw new Error('Ngày này chưa được công bố.');return entry;}
  async partition(item){
    if(!/^\d{4}-\d{2}-\d{2}\/(market|history-[0-9a-f])-[0-9a-f]{16}\.json\.gz$/.test(item.path)||!/^[0-9a-f]{64}$/.test(item.sha256))throw new Error('Đường dẫn dữ liệu không hợp lệ.');
    const cacheKey=item.path+'|'+item.sha256;
    if(this.cache.has(cacheKey)){const value=this.cache.get(cacheKey);this.cache.delete(cacheKey);this.cache.set(cacheKey,value);return value;}
    const promise=(async()=>{
      const url=new URL('./data/'+item.path,location.href);
      const response=await fetch(url,{credentials:'omit'});
      if(!response.ok)throw new Error('Không tải được lịch sử đã chọn. Hãy thử tải lại dữ liệu.');
      const bytes=await response.arrayBuffer();
      const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('');
      if(hash!==item.sha256)throw new Error('Dữ liệu tải về không khớp checksum; chưa dùng để tính toán.');
      if(typeof DecompressionStream==='undefined')throw new Error('Cần trình duyệt có hỗ trợ giải nén gzip. Hãy cập nhật Chrome, Edge hoặc Firefox.');
      const text=await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
      const payload=JSON.parse(text);
      if(!Array.isArray(payload.records)||payload.records.length!==item.records)throw new Error('Số snapshot không khớp danh mục công bố.');
      return payload;
    })();
    this.cache.set(cacheKey,promise);
    while(this.cache.size>6)this.cache.delete(this.cache.keys().next().value);
    try{return await promise;}catch(error){this.cache.delete(cacheKey);throw error;}
  }
  async market(day){return this.partition(this.day(day).market);}
  async prepare(day,symbols){
    const entry=this.day(day),wanted=[...new Set(symbols)].sort();
    const groups=[...new Set(wanted.map(s=>([...s].reduce((n,c)=>n+c.charCodeAt(0),0)%16).toString(16)))];
    const [market,...histories]=await Promise.all([this.market(day),...groups.map(g=>this.partition(entry.groups[g]))]);
    const wantedSet=new Set(wanted),records=new Map(market.records.map(r=>[r.event_id,r]));
    for(const history of histories)for(const r of history.records)if(wantedSet.has(r.symbol))records.set(r.event_id,r);
    if(records.size>30000)throw new Error('Danh mục cần hơn 30.000 snapshot mẫu. Hãy chia danh mục thành các nhóm nhỏ hơn.');
    return {key:entry.fingerprint+'|'+wanted.join(','),payload:{...market,records:[...records.values()]}};
  }
}
