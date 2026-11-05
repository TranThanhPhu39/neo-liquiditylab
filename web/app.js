'use strict';
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Currency totals stay decimal strings, including values above JS's safe integer range.
const format = (value, digits = 0) => {
  if(value===null||value===undefined||value==='None')return '—';
  const m=String(value).match(/^(-?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/);
  if(!m)return '—';
  const fraction=m[3]||'',scale=fraction.length-Number(m[4]||0);
  let integer=BigInt(m[2]+fraction);
  if(scale>digits){const divisor=10n**BigInt(scale-digits);integer=(integer+divisor/2n)/divisor;}
  else integer*=10n**BigInt(digits-scale);
  const s=integer.toString().padStart(digits+1,'0'),whole=digits?s.slice(0,-digits):s;
  return (m[1]&&integer!==0n?'-':'')+whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(digits?'.'+s.slice(-digits):'');
};
const pct = value => value === null || value === undefined ? '—' : `${format(Number(value) * 100, 1)}%`;
const localTime = value => value ? new Intl.DateTimeFormat('vi-VN', {timeZone:'Asia/Ho_Chi_Minh',dateStyle:'short',timeStyle:'medium'}).format(new Date(value)) : '—';
const labels = {liquidity:['Thanh khoản','Quan sát độ sâu sổ lệnh, chênh lệch giá và phạm vi dữ liệu.'],execution:['Mô phỏng thực hiện lệnh','A1 · Ước tính quét ba mức giá trên phần khối lượng quan sát được.'],stress:['Stress thanh khoản danh mục','B5 · So sánh mức bao phủ khi độ sâu mua suy giảm theo kịch bản giả định.']};
const state = {page:'liquidity',loaded:false,demo:false,data:null,days:[],symbols:[],version:0,workerReady:false};
const cloud = new LiquidityCloud();
let cloudEpoch=0;
const worker = new Worker('./engine-worker.js');
const pending = new Map(); let nextId = 0;
function call(method, text) {return new Promise((resolve,reject) => {const id=++nextId;pending.set(id,{resolve,reject});worker.postMessage({id,method,text});});}
worker.onmessage = ({data}) => {
  if(data.ready){state.workerReady=true;$('engineState').textContent='Bộ tính sẵn sàng';return;}
  if(data.fatal){$('engineState').textContent='Bộ tính chưa sẵn sàng';notice('Không tải được bộ tính. Hãy tải lại trang để thử lại.');return;}
  const item=pending.get(data.id);if(!item)return;pending.delete(data.id);data.error?item.reject(new Error(data.error)):item.resolve(data.result);
};
worker.onerror=()=>{notice('Bộ tính gặp lỗi tải. Hãy tải lại trang.');$('engineState').textContent='Lỗi tải bộ tính';};
function notice(text){$('message').className=text?'alert':'';$('message').textContent=text;}
function alertIn(id,text){$(id).className=text?'alert':'';$(id).textContent=text;}
function metric(label,value,note=''){return `<div class="metric"><div class="metric-label">${escapeHTML(label)}</div><div class="metric-value">${escapeHTML(value)}</div><div class="metric-note">${escapeHTML(note)}</div></div>`;}
function table(headers,rows){return `<div class="table-scroll"><table><thead><tr>${headers.map(h=>`<th>${escapeHTML(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${escapeHTML(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
function clearCharts(ids,message='Chưa có kết quả'){for(const id of ids)$(id).textContent=message;}
function optionList(id,values,value){$(id).replaceChildren(...values.map(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;return o;}));$(id).value=values.includes(value)?value:values[0];$(id).disabled=!values.length;}
function page(){const key=location.hash.slice(1);state.page=labels[key]?key:'liquidity';$('title').textContent=labels[state.page][0];$('breadcrumb').textContent=labels[state.page][0];$('description').textContent=labels[state.page][1];document.querySelectorAll('[data-page]').forEach(a=>{a.classList.toggle('active',a.dataset.page===state.page);a.setAttribute('aria-current',a.dataset.page===state.page?'page':'false');});document.querySelectorAll('.screen').forEach(s=>s.hidden=s.id!==state.page);update().then(()=>window.scrollTo(0,0));}
function validNumber(id,min,max,integer=false){const v=$(id).value;if(v.trim()==='')throw new Error('Vui lòng nhập đủ thông số.');const n=Number(v);if(!Number.isFinite(n)||n<min||n>max||integer&&!Number.isSafeInteger(n))throw new Error(`Giá trị ${$(id).previousSibling?.textContent?.trim()||'đã nhập'} không hợp lệ.`);return n;}
function request(reverse=false){return {day:$('day').value,symbol:$('symbol').value,time:$('clock').value||'23:59',page:state.page,
  side:$('side').value,quantity:validNumber(reverse?'reverseQty':'quantity',1,1e12,true),benchmark:$('benchmark').value,
  fee_percent:(validNumber('fee',0,100),$('fee').value),tax_percent:(validNumber('tax',0,100),$('tax').value),portfolio:$('portfolio').value,
  shock:validNumber('shock',0,90,true),threshold:validNumber('threshold',1,100),
  depth_side:$('depthSide').value,depth_mode:$('depthMode').value,reverse};}
async function update(reverse=false){
  if(!state.loaded)return;const version=++state.version;
  if(!reverse)$('reverseResult').textContent='Tìm lại ngưỡng sau khi thay đổi thông số.';
  try {
    const query=request(reverse);
    if(state.page==='liquidity'){
      const scope=[query.day,query.symbol,query.time].join('|'),element=$('depthSurface');
      if(element.dataset.scope!==scope){DepthSurface.clear(element,'Đang chọn dữ liệu lịch sử…');$('depthSummary').textContent='Đang tải';element.dataset.scope=scope;}
    }
    if(cloud.enabled){
      const positions=query.page==='stress'?await call('portfolio_symbols',query.portfolio):[];
      const prepared=await cloud.prepare(query.day,[query.symbol,...positions]);
      if(version!==state.version||!cloud.enabled)return;
      if(cloud.hydrated!==prepared.key){
        await call('ingest',JSON.stringify(prepared.payload));
        cloud.hydrated=prepared.key;
      }
      if(version!==state.version||!cloud.enabled)return;
    }
    const result=await call('view',JSON.stringify(query));
    if(version!==state.version)return;
    state.data=result;notice('');render(result);
    if(reverse)$('reverseResult').textContent=result.stress_error||result.execution_error||(result.reverse===null?'Coverage không rơi dưới ngưỡng trong khoảng giảm độ sâu 0–90%.':result.reverse===undefined?'Không đủ dữ liệu hợp lệ cho reverse stress.':`Ngưỡng suy giảm độ sâu: ${pct(result.reverse)} (lưới 0,01%).`);
  }catch(error){if(version!==state.version)return;notice(error.message);clearFinancial();DepthSurface.clear($('depthSurface'),'Chưa có dữ liệu hợp lệ cho lựa chọn này.');$('depthSummary').textContent='Chưa có dữ liệu';if(cloud.enabled){state.data=null;$('liquidityMetrics').replaceChildren();$('export').disabled=true;clearCharts(['book','depthChart','quality','historyChart','watchlist'],'Chưa tải được dữ liệu hợp lệ cho lựa chọn này.');$('raw').textContent='Chưa có snapshot hợp lệ.';$('provenance').textContent='Chưa dùng dữ liệu để tính toán. Hãy thử tải lại ngày hoặc mã đã chọn.';}}
}
function clearFinancial(){$('executionMetrics').replaceChildren();$('baseline').replaceChildren();$('stressed').replaceChildren();clearCharts(['fills','costChart','coverageChart','portfolioChart','portfolioRows','exclusions'],'Chưa có kết quả hợp lệ.');}
function render(r){
  if(state.page==='liquidity'){
    DepthSurface.render($('depthSurface'),r.depth_surface);
    const d=r.depth_surface;
    $('depthSummary').textContent=d?.points?`${d.points} snapshot mẫu · ${d.gaps} khoảng ngắt${d.units_verified?'':' · Đơn vị chưa xác minh độc lập'}`:'Chưa có snapshot';
    $('depthSurfaceNote').textContent=(d?.points===1?'Chỉ có một snapshot: hiển thị lát cắt ba mức, chưa đủ mặt theo thời gian. ':'')+'Mặt lưới nối điểm mẫu của ba mức mỗi bên. Khoảng cách nguồn trên 15 phút và phần thiếu được ngắt; không khôi phục mức giá hoặc tick chưa quan sát.';
  }
  $('empty').hidden=true;$('workspace').hidden=false;$('export').disabled=!r.record;
  const b=r.record;
  const wsBook=b?.capture_method==='websocket_realtime';
  $('dataTag').textContent=state.demo?'DỮ LIỆU MẪU':wsBook?'DNSE · WS ĐÃ LƯU':cloud.enabled?'DNSE · LỊCH SỬ LẤY MẪU':'DNSE · LỊCH SỬ';
  $('provenance').textContent=(state.demo?'DỮ LIỆU GIẢ LẬP · ':wsBook?'QUAN SÁT WS ĐÃ LƯU · ':'TÁI DỰNG LỊCH SỬ · ')+(b?`Nguồn: ${localTime(b.source_timestamp)} · ${wsBook?'Nhận WS':'Nhận khi tải'}: ${localTime(b.collected_at_utc)}`:'Chưa có snapshot của mã trước mốc chọn; không dùng dữ liệu tương lai.')+(cloud.enabled?' · Mẫu lịch sử, không phải dữ liệu realtime.':'');
  if(!b){$('liquidityMetrics').replaceChildren();clearCharts(['book','depthChart','quality','historyChart'],'NO DATA · Không có snapshot trước thời điểm đã chọn.');$('raw').textContent='Không có snapshot.';clearFinancial();alertIn('executionMessage','NO DATA · Chưa thể mô phỏng mã này.');}
  else {
    const m=r.metrics||{};
    $('liquidityMetrics').innerHTML=metric('Độ sâu Bid · 3 mức',format(m.bid_depth),'CP quan sát được')+metric('Độ sâu Ask · 3 mức',format(m.ask_depth),'CP quan sát được')+metric('Relative spread',pct(m.relative_spread),'Spread / Giá giữa')+metric('Mất cân bằng sổ lệnh',format(m.obi,4),'OBI · Trong khoảng −1 đến 1');
    $('bookSymbol').textContent=b.symbol+' · '+b.exchange;
    $('book').innerHTML=`<table><thead><tr><th>Mức</th><th>KL mua</th><th>Giá mua</th><th>Giá bán</th><th>KL bán</th></tr></thead><tbody>${[1,2,3].map(i=>`<tr><td>0${i}</td><td>${format(b['bid_vol_'+i])}</td><td class="book-bid">${format(b['bid_price_'+i])}</td><td class="book-ask">${format(b['ask_price_'+i])}</td><td>${format(b['ask_vol_'+i])}</td></tr>`).join('')}</tbody></table>`;
    const cumulative=side=>{let sum=0;return[1,2,3].map(i=>{if(b[`${side}_price_${i}`]===null||b[`${side}_vol_${i}`]===null)return null;sum+=b[`${side}_vol_${i}`];return sum;});};
    chart('depthChart',['Mức 1','Mức 2','Mức 3'],[{name:'Bid',values:cumulative('bid'),color:'#252525'},{name:'Ask',values:cumulative('ask'),color:'#aaa'}]);
    const quality=[['Đơn vị',state.demo?'GIẢ LẬP':b.unit_verification_status],['Chất lượng',b.data_quality_status],['Phiên nguồn',b.market_session],['A1 / B5',b.financial_eligible?'Đủ điều kiện đơn vị / phiên':'Chưa đủ điều kiện'],['Giá / Khối lượng','VND / CP'],['Điểm đến mốc chọn',format(r.rows)]];
    $('quality').innerHTML=quality.map(([k,v])=>`<div class="quality-row"><span>${escapeHTML(k)}</span><strong>${escapeHTML(v)}</strong></div>`).join('')+`<p class="small muted" style="margin-top:14px">${escapeHTML(r.quality_error||(wsBook?'WS đã lưu: kiểm chứng ba mức trong từng thông điệp đầy đủ. Không ghép mức thiếu và không bảo đảm đã quan sát toàn phiên.':cloud.enabled?'Đơn vị theo hồ sơ chuẩn hóa, kiểm chứng riêng từng mã. Dữ liệu lịch sử tải sau phiên.':'Nhãn xác minh theo tệp đã nhập. Dữ liệu tải sau phiên; không chứng minh đã nhận tại thời điểm thị trường đó.'))}</p>`;
    const h=r.history||[];
    h.length>1?chart('historyChart',h.map(x=>localTime(x.source_local_time)),[{name:'Bid',values:h.map(x=>x.bid_observed_depth_shares),color:'#252525'},{name:'Ask',values:h.map(x=>x.ask_observed_depth_shares),color:'#aaa'}]):clearCharts(['historyChart'],'Chưa đủ điểm lịch sử trước thời gian đã chọn để vẽ biểu đồ.');
    $('raw').textContent=JSON.stringify(b,null,2);
    if(state.page==='execution')renderExecution(r);
  }
  if(state.page==='stress')renderStress(r);
  renderWatchlist();
}
function renderWatchlist(){if(!state.data)return;const term=$('search').value.trim().toUpperCase();const rows=state.data.watchlist.filter(r=>r.symbol.includes(term));$('watchlist').innerHTML=`<table><thead><tr><th>Mã / Sàn</th><th>Bid (CP)</th><th>Ask (CP)</th><th>A1 / B5</th></tr></thead><tbody>${rows.map(r=>`<tr><td><button class="symbol-button" data-symbol="${escapeHTML(r.symbol)}">${escapeHTML(r.symbol)}</button> <small class="muted">${escapeHTML(r.exchange)}</small></td><td>${format(r.bid_observed_depth_shares)}</td><td>${format(r.ask_observed_depth_shares)}</td><td>${!r.has_snapshot?'NO DATA':r.financial_eligible?'Đủ điều kiện':'Chưa xác minh / ngoài phiên'}</td></tr>`).join('')}</tbody></table>`;if(!rows.length)$('watchlist').textContent='Không có mã khớp bộ lọc tại thời điểm này.';}
function renderExecution(r){
  const allowed=$('consentA1').checked;
  const error=!allowed?'Bật cho phép phân tích snapshot lịch sử để xem mô phỏng.':r.execution_error;
  alertIn('executionMessage',error||'');
  if(error||!r.execution){$('executionMetrics').replaceChildren();clearCharts(['fills','costChart','coverageChart']);$('executionNote').textContent='A1 chỉ dùng mã đã xác minh đơn vị, sổ lệnh hợp lệ và phiên liên tục.';return;}
  const e=r.execution;
  $('executionMetrics').innerHTML=metric('Bao phủ',pct(e.coverage_ratio),`${format(e.executed_quantity)} / ${format(e.requested_quantity)} CP`)+metric('VWAP phần bao phủ',format(e.vwap,2),'VND / CP')+metric('Chi phí so benchmark',format(e.book_walking_cost),'VND · Chỉ phần được bao phủ')+metric('Chưa bao phủ',format(e.uncovered_quantity),'CP · Chưa định giá');
  $('executionNote').textContent=`Gross: ${format(e.gross_value)} VND · Net: ${format(e.net_value)} VND · ${format(e.cost_bps,2)} bps · Phí: ${format(e.fees)} · Thuế: ${format(e.sell_tax)}. Chi phí và net chỉ trên phần được bao phủ.`;
  $('fills').innerHTML=table(['Mức','Giá (VND)','Khối lượng (CP)','Giá trị (VND)'],e.fills.map((f,i)=>[f.level,format(f.price),format(f.quantity),format(e.fill_values[i])]));
  chart('costChart',r.curve.map(x=>format(x.quantity)),[{name:'Chi phí phần bao phủ',values:r.curve.map(x=>x.cost),color:'#252525'}]);
  chart('coverageChart',r.curve.map(x=>format(x.quantity)),[{name:'Coverage (%)',values:r.curve.map(x=>Number(x.coverage)*100),color:'#252525'}],100);
}
function renderStress(r){
  const error=!$('consentB5').checked?'Bật cho phép phân tích snapshot lịch sử để xem stress danh mục.':r.stress_error;
  alertIn('stressMessage',error||'');
  if(error||!r.baseline){$('baseline').innerHTML='<h2>BASELINE</h2><p class="muted small">Chưa có kết quả.</p>';$('stressed').innerHTML='<h2>STRESS</h2><p class="muted small">Chưa có kết quả.</p>';clearCharts(['portfolioChart','portfolioRows','exclusions']);return;}
  for(const [id,title] of [['baseline','BASELINE'],['stressed',`STRESS · GIẢM ${$('shock').value}% BID`]]){const x=r[id];$(id).innerHTML=`<h2>${escapeHTML(title)}</h2><span class="small muted">Coverage phần đủ dữ liệu</span><div class="big">${pct(x.coverage)}</div><span class="small muted">${format(x.covered)} / ${format(x.analyzed_quantity)} CP</span><div class="submetric"><span class="small muted">Chi phí phần tính được (VND)</span><strong>${format(x.cost)}</strong></div><span class="small muted">Chưa bao phủ: ${format(x.uncovered)} CP · Loại: ${format(x.excluded_quantity)} CP</span>`;}
  const rows=r.baseline.rows;
  chart('portfolioChart',rows.map(x=>x.symbol),[{name:'Baseline (%)',values:rows.map(x=>x.coverage*100),color:'#252525'},{name:'Stress (%)',values:rows.map(x=>(r.stressed.rows.find(y=>y.symbol===x.symbol)?.coverage??0)*100),color:'#aaa'}],100,true);
  const exclusions=Object.entries(r.baseline.exclusions);
  $('exclusions').textContent=exclusions.length?'Bị loại khỏi mẫu số coverage và tổng tiền: '+exclusions.map(([s,x])=>`${s}: ${x.reason} (${format(x.quantity)} CP)`).join(' · '):'Tất cả vị thế đủ điều kiện trong phạm vi tệp.';
  if(r.baseline.reference_missing.length)$('exclusions').textContent+=' Không có giá tham chiếu/khớp để định giá toàn danh mục.';
  $('exclusions').className='small muted';
  $('portfolioRows').innerHTML=table(['Mã','Yêu cầu (CP)','Bao phủ (CP)','Chưa bao phủ','Chi phí (VND)'],rows.map(x=>[x.symbol,format(x.requested),format(x.covered),format(x.uncovered),format(x.cost)]));
}
function chart(id,labels,series,fixedMax,bars=false){
  if(!labels.length){$(id).textContent='Chưa có điểm dữ liệu đủ điều kiện.';return;}
  const w=600,h=250,left=62,right=22,top=30,bottom=42,iw=w-left-right,ih=h-top-bottom;
  const numbers=series.flatMap(s=>s.values).filter(x=>x!==null&&x!==undefined).map(Number).filter(Number.isFinite);
  if(!numbers.length){$(id).textContent='Chưa có mức giá và khối lượng hợp lệ.';return;}
  const max=fixedMax||Math.max(1,...numbers)*1.1;
  const x=i=>left+(labels.length===1?iw/2:i*iw/(labels.length-1));const y=v=>top+ih-Number(v)/max*ih;
  const compact=v=>v>=1e9?format(v/1e9,1)+'B':v>=1e6?format(v/1e6,1)+'M':v>=1e3?format(v/1e3,0)+'K':format(v);
  let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${escapeHTML(series.map(s=>s.name).join(', '))}"><g font-family="Rubik,Arial" font-size="10" fill="#888">`;
  for(let i=0;i<=4;i++){const value=max*i/4;svg+=`<line x1="${left}" x2="${w-right}" y1="${y(value)}" y2="${y(value)}" stroke="#ededed"/><text x="${left-10}" y="${y(value)+3}" text-anchor="end">${compact(value)}</text>`;}
  const steps=[...new Set([0,Math.floor((labels.length-1)/2),labels.length-1])];
  for(const i of steps)svg+=`<text x="${x(i)}" y="${h-15}" text-anchor="middle">${escapeHTML(labels[i])}</text>`;
  series.forEach((s,j)=>{let segment=[];const segments=[];s.values.forEach((v,i)=>{if(v===null||v===undefined||!Number.isFinite(Number(v))){if(segment.length)segments.push(segment);segment=[];}else segment.push([x(i),y(v),i,v]);});if(segment.length)segments.push(segment);for(const points of segments){if(bars){const bw=Math.min(45,iw/(labels.length*3));for(const [xx,yy,i,v] of points){const center=left+(i+.5)*iw/labels.length;svg+=`<rect x="${center+(j-series.length/2)*bw}" y="${yy}" width="${bw-3}" height="${top+ih-yy}" fill="${s.color}"><title>${escapeHTML(s.name+' · '+labels[i]+': '+format(v,2))}</title></rect>`;}}else{svg+=`<polyline fill="none" stroke="${s.color}" stroke-width="2" points="${points.map(p=>p[0]+','+p[1]).join(' ')}"/>`;for(const [xx,yy,i,v] of points)svg+=`<circle cx="${xx}" cy="${yy}" r="${labels.length>40?1.5:3}" fill="${s.color}"><title>${escapeHTML(s.name+' · '+labels[i]+': '+format(v,2))}</title></circle>`;}}svg+=`<rect x="${left+j*170}" y="5" width="12" height="3" fill="${s.color}"/><text x="${left+18+j*170}" y="10">${escapeHTML(s.name)}</text>`;});
  $(id).innerHTML=svg+'</g></svg>';
}
async function ingest(text,name,demo=false){
  const previousCloud=cloud.enabled;
  cloud.enabled=false;cloudEpoch++;state.version++;
  notice('Đang kiểm tra và mở dữ liệu…');
  try{const parsed=JSON.parse(text);const info=await call('ingest',text);state.loaded=true;state.demo=demo;state.days=info.days;
    optionList('day',info.days,info.days.at(-1));state.symbols=info.symbols[$('day').value];
    state.daySymbols=info.symbols;optionList('symbol',state.symbols,state.symbols.includes('ACB')?'ACB':state.symbols[0]);
    const cutoff=parsed.cutoff?.match(/T(\d\d:\d\d)/)?.[1];$('clock').value=cutoff||'14:20';$('sourceName').textContent=demo?'Mẫu giả lập · 2 mã':'DNSE · Lịch sử chuẩn hóa';$('sourceDetail').textContent=`${name} · ${format(info.count)} snapshot · Xử lý trong tab này`;$('consentA1').checked=false;$('consentB5').checked=false;$('reverseResult').textContent='Tìm mức giảm độ sâu nhỏ nhất trên lưới 0,01%, trong khoảng 0–90%.';await update();
    if(parsed.records?.some(r=>r.capture_method==='websocket_realtime'))$('sourceName').textContent='DNSE · WS đã lưu';
  }catch(error){cloud.enabled=previousCloud;notice('Không mở được tệp: '+error.message);if(previousCloud&&!state.loaded)startCloud();}
}
async function loadCloudDay(day,preferredSymbol='ACB'){
  const epoch=++cloudEpoch;
  cloud.enabled=true;cloud.hydrated=null;state.loaded=false;state.version++;
  $('workspace').hidden=true;$('empty').hidden=false;$('emptyTitle').textContent='Đang tải dữ liệu '+day+'…';
  notice('Đang tải dữ liệu DNSE đã chuẩn hóa…');
  try{
    const payload=await cloud.market(day);
    if(epoch!==cloudEpoch||!cloud.enabled)return;
    const symbols=Object.keys(payload.catalog).sort();
    state.days=cloud.index.days.map(d=>d.date);state.symbols=symbols;state.demo=false;
    optionList('day',state.days,day);optionList('symbol',symbols,preferredSymbol);
    $('clock').value=cloud.day(day).preferred_time;
    $('consentA1').checked=false;$('consentB5').checked=false;
    $('sourceName').textContent='DNSE · Dữ liệu web';
    $('sourceDetail').textContent=`${day} · ${format(symbols.length)} mã · Lịch sử lấy mẫu · Cập nhật công bố: ${localTime(cloud.index.generated_at)}`;
    state.loaded=true;$('workspace').hidden=false;$('empty').hidden=true;clearFinancial();$('liquidityMetrics').replaceChildren();await update();
  }catch(error){if(epoch===cloudEpoch){$('emptyTitle').textContent='Chưa tải được dữ liệu web';notice(error.message);}}
}
async function startCloud(){
  const epoch=++cloudEpoch;cloud.enabled=true;state.version++;notice('Đang kiểm tra ngày dữ liệu mới nhất…');
  try{const index=await cloud.initialize();if(epoch!==cloudEpoch||!cloud.enabled)return;await loadCloudDay(index.latest_day);}
  catch(error){if(epoch===cloudEpoch){$('emptyTitle').textContent='Chưa tải được dữ liệu web';notice(error.message);}}
}
function demo(){const records=[];for(const symbol of ['ACB','FPT'])for(let n=0;n<8;n++){const t=`2026-10-28T13:${String(10+n*10).padStart(2,'0')}:00+07:00`;const time=new Date(Date.parse('2026-10-28T13:10:00+07:00')+n*600000).toISOString();let r={symbol,exchange:'HOSE',provider:'dnse',normalization_version:'dnse-canonical-v1',upstream_source:'dnse_openapi',capture_method:'synthetic_demo',provider_version:'2026-07-23',source_timestamp:time,source_local_time:new Date(Date.parse(time)+25200000).toISOString().slice(0,-1)+'+07:00',collected_at_utc:'2026-10-28T10:00:00+00:00',market_session:'DNSE_CONTINUOUS_PM',unit_verification_status:symbol==='ACB'?'EMPIRICALLY_VERIFIED':'ASSUMED_SHARED_SCHEMA',unit_contract_hash:'synthetic-demo-only',price_unit:'VND',volume_unit:'shares',issues:[],data_quality_status:'VALID',local_date:'2026-10-28',event_id:`demo-${symbol}-${n}`};let bid=0,ask=0;for(let i=1;i<=3;i++){r['bid_price_'+i]=String(21250-i*50);r['ask_price_'+i]=String(21200+i*50);r['bid_vol_'+i]=[113300,22000,165800][i-1]+n*200;r['ask_vol_'+i]=[56200,173100,120500][i-1]+n*300;bid+=r['bid_vol_'+i];ask+=r['ask_vol_'+i];}r.bid_observed_depth_shares=bid;r.ask_observed_depth_shares=ask;records.push(r);}return ingest(JSON.stringify({records,cutoff:'2026-10-28T14:20:59+07:00'}),'Dữ liệu mẫu để thử chức năng',true);}
document.querySelectorAll('[data-import]').forEach(b=>b.addEventListener('click',()=>$('file').click()));
$('file').addEventListener('change',async e=>{const f=e.target.files[0];if(!f)return;if(f.size>25*1024*1024){notice('Tệp lớn hơn 25 MB. Hãy xuất ít mã/điểm mẫu hơn.');return;}await ingest(await f.text(),f.name);e.target.value='';});
$('demo').addEventListener('click',demo);$('emptyDemo').addEventListener('click',demo);
$('day').addEventListener('change',()=>{if(cloud.enabled){loadCloudDay($('day').value,$('symbol').value);return;}optionList('symbol',state.daySymbols[$('day').value],$('symbol').value);update();});
for(const id of ['symbol','clock','side','quantity','benchmark','fee','tax','consentA1','portfolio','consentB5','threshold','reverseQty','depthSide','depthMode'])$(id).addEventListener('change',()=>{$('reverseResult').textContent='Tìm lại ngưỡng sau khi thay đổi thông số.';update();});
let debounce;$('shock').addEventListener('input',()=>{$('shockValue').value=$('shock').value;$('reverseResult').textContent='Tìm lại ngưỡng sau khi thay đổi thông số.';clearTimeout(debounce);debounce=setTimeout(update,100);});
$('search').addEventListener('input',renderWatchlist);$('watchlist').addEventListener('click',e=>{const b=e.target.closest('[data-symbol]');if(b){$('symbol').value=b.dataset.symbol;update();}});
$('reverse').addEventListener('click',()=>{if(!$('consentB5').checked){alertIn('stressMessage','Cho phép phân tích lịch sử trước khi tìm reverse stress.');return;}update(true);});
$('export').addEventListener('click',()=>{if(!state.data?.record)return;const url=URL.createObjectURL(new Blob([JSON.stringify(state.data.record,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`${state.demo?'DEMO_':''}dnse_${state.data.record.symbol}_${$('day').value}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
$('help').addEventListener('click',()=>$('helpDialog').showModal());$('closeHelp').addEventListener('click',()=>$('helpDialog').close());
$('footerHelp').addEventListener('click',()=>$('helpDialog').showModal());
$('cloudReload').addEventListener('click',startCloud);
window.addEventListener('hashchange',page);page();startCloud();
