'use strict';
(()=>{
  const $=id=>document.getElementById(id),M=SSILiveModel,buffer=new M.Window();
  const fmt=x=>x===null||x===undefined?'—':Number(x).toLocaleString('vi-VN',{maximumFractionDigits:3});
  const clock=t=>new Date(t).toLocaleTimeString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',hour12:false});
  let csrf='',status={},runs=[],follow=true,index=0,side='both',layout=null,playing=false,playWall=0,playOrigin=0,eventId=null,requestId=0,lastPaint=0,noteRows=[];
  const states={IDLE:'Chưa thu',WAITING_FOR_COLLECTOR:'Chờ bộ thu theo lịch',RELAY_STALE:'Luồng cập nhật web bị gián đoạn',STARTING:'Đang bắt đầu',CONNECTING:'Đang kết nối',CONNECTED_WAITING_DATA:'Đã kết nối · Chờ dữ liệu',RECEIVING:'Đang nhận dữ liệu',DISCONNECTED:'Mất kết nối',RECONNECTING:'Đang kết nối lại',STOPPED:'Đã dừng',FAILED:'Kết nối thất bại'};
  const types={SPREAD_WIDENING:'Spread mở rộng',DEPTH_DROP:'Độ sâu giảm',IMBALANCE_PERSISTENCE:'Mất cân đối kéo dài'};
  const reasons={UNVERIFIED_UNITS:'Chưa đối chiếu đơn vị riêng cho mã này',OUTSIDE_CONTINUOUS_SESSION:'Chưa xác nhận phiên khớp lệnh liên tục từ SSI',INVALID_OBSERVATION:'Bản tin thiếu dữ liệu, chưa rõ cách cập nhật hoặc đồng hồ không phù hợp',OUT_OF_ORDER_SOURCE_TIME:'Thời gian nguồn đi lùi',OBSERVATION_GAP:'Có khoảng trống quan sát'};
  const detectorStates={WARMING_UP:'Đang tích lũy đường cơ sở',NORMAL:'Đang theo dõi',INHIBITED:'Tạm ngưng',CANDIDATE:'Đang kiểm tra diễn biến',ACTIVE:'Có sự kiện đang diễn ra',RECOVERING:'Đang hồi phục'};
  async function api(path,body){const response=await fetch(path,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-Monitor-CSRF':csrf},body:JSON.stringify(body)});const d=await response.json();if(!response.ok)throw Error(d.error||'Không đọc được dữ liệu.');return d;}
  function fail(e){$('error').hidden=false;$('error').textContent=e.message;}
  function query(){return 'run_id='+encodeURIComponent($('run').value)+'&symbol='+$('symbol').value;}
  function setText(id,text){$(id).textContent=text;}
  async function refreshRuns(){
    const selected=$('run').value; runs=await api('/api/live/runs');$('run').replaceChildren();
    if(!runs.length)$('run').add(new Option('Chưa có phiên SSI',''));
    for(const r of runs)$('run').add(new Option(r.day+' · '+clock(r.created_at)+' · '+r.state,r.run_id));
    if(runs.some(r=>r.run_id===selected))$('run').value=selected;
    else if(status.run_id)$('run').value=status.run_id;
  }
  async function load(start=null){
    if(!$('run').value)return;
    const suffix=start===null?'&after=-1':'&after=-1&start='+Math.max(0,start);
    buffer.accept(await api('/api/live/view?'+query()+suffix));index=Math.max(0,buffer.frames.length-1);playing=false;
    if(start!==null)follow=false;
    eventId=null;noteRows=[];$('review').hidden=true;$('review').dataset.event='';$('export').hidden=true;$('evidence').textContent='Chọn một sự kiện để xem bằng chứng.';render();await events();
  }
  async function events(){
    const f=buffer.frames[index];if(!f)return;
    const version=++requestId,run=buffer.run;
    const cutoff=follow&&status.active?new Date().toISOString():f.time;
    const list=await api('/api/live/events?'+query()+'&at='+encodeURIComponent(cutoff));
    if(version!==requestId||run!==buffer.run)return;
    buffer.events=list;renderEvents();paint();
  }
  function paint(){
    if(!buffer.frames.length)return;
    const live=follow&&status.active&&status.run_id===buffer.run;
    const g=buffer.geometry(index,Number($('window').value),side,live?Date.now():null);
    const b=buffer.bubbles(g,Number($('group').value));
    layout=MonitorHeatmap.draw($('heatmap'),g,Number($('scale').value),$('bubbles').checked?b.rows:[],10000,buffer.units!=='EMPIRICALLY_VERIFIED'||buffer.trades.some(t=>t.unit_status!=='EMPIRICALLY_VERIFIED'));
    setText('bubbleNote',b.scaleMismatch?'Giá quote/trade có khác thang số; đã ẩn phần không tương thích.':b.rows.length?fmt(b.rows.length)+' nhóm khớp trong cửa sổ · màu trung tính nếu chưa xác minh':'Chưa có giao dịch khớp trong cửa sổ');
    if(buffer.trades.length>=10000)setText('bubbleNote',$('bubbleNote').textContent+' · Có thể chạm giới hạn giao dịch của đoạn đang tải; số hiện trên biểu đồ không phải tổng khớp toàn phiên.');
  }
  function renderEvents(){
    const f=buffer.frames[index],reason=f?.features.reason||f?.features.quality_reason;
    setText('detector',!f?'Chờ sổ lệnh.':f.features.eligible?'Quy tắc thử nghiệm · '+(detectorStates[f.features.detector_state]||f.features.detector_state):'Chưa đủ điều kiện: '+(reasons[reason]||'Cần thêm bằng chứng về chất lượng dữ liệu'));
    $('events').replaceChildren();const cutoff=f?M.time(f.time):0;
    const rows=buffer.events.filter(e=>M.time(e.detected_received_at||e.observed_at)<=cutoff);
    for(const e of rows.sort((a,b)=>b.detected_index-a.detected_index)){
      const button=document.createElement('button');button.className='event-card';button.textContent=(types[e.type]||e.type)+' · '+(e.side||'')+' · '+e.state+' · '+clock(e.detected_received_at||e.observed_at);
      button.onclick=async()=>{try{follow=false;playing=false;eventId=e.id;let i=buffer.frames.findIndex(f=>f.index===e.detected_index);if(i<0){await load(Math.max(0,e.detected_index-100));i=buffer.frames.findIndex(f=>f.index===e.detected_index);eventId=e.id;}index=Math.max(0,i);buffer.events=[];await events();render();}catch(error){fail(error);}};
      $('events').append(button);
    }
    if(!rows.length){const p=document.createElement('p');p.className='subtle';p.textContent='Chưa có sự kiện đủ điều kiện đến mốc đang xem.';$('events').append(p);}
    const current=rows.find(e=>e.id===eventId);if(current)showEvidence(current);
  }
  function showEvidence(event){
    $('evidence').replaceChildren();const pre=document.createElement('pre');pre.textContent=JSON.stringify(event,null,2);$('evidence').append(pre);$('review').hidden=false;
    $('export').hidden=false;$('export').href='/api/live/export?'+query()+'&at='+encodeURIComponent(buffer.frames[index].time);
    const selection=buffer.run+'|'+event.id;
    if($('review').dataset.event!==selection){
      $('review').dataset.event=selection;noteRows=[];$('note').value='';$('verdict').value='uncertain';setText('reviewState','');
      api('/api/live/reviews?'+query()).then(rows=>{if($('review').dataset.event!==selection)return;const frame=buffer.frames[index],current=buffer.events.find(e=>e.id===event.id&&frame&&M.time(e.observed_at)<=M.time(frame.time));if(!current)return;noteRows=rows.filter(r=>r.event_id===event.id);const last=noteRows.at(-1);if(last){$('note').value=last.note;$('verdict').value=last.verdict;}showEvidence(current);}).catch(fail);
    }
    for(const note of noteRows){const p=document.createElement('p');p.className='review-entry';p.textContent=clock(note.created_at)+' · '+note.verdict+'\n'+note.note;$('evidence').append(p);}
  }
  function render(){
    const f=buffer.frames[index];$('cursor').disabled=!f;$('cursor').max=Math.max(0,buffer.frames.length-1);$('cursor').value=index;
    setText('position',f?'Quan sát '+fmt(f.index+1)+' / '+fmt(buffer.total)+' · bộ thu vẫn lưu độc lập':'Chưa có quan sát');
    setText('viewMode',follow?'Theo dõi hiện tại':'Đang xem lại · bộ thu vẫn chạy');
    const observed=f?f.bids.filter(p=>p[0]!==null).length+' mua / '+f.asks.filter(p=>p[0]!==null).length+' bán':'Đang đếm mức quan sát';
    setText('units',observed+' · '+(buffer.priceUnit==='VND'?'Giá VND':'Giá nguồn SSI')+' · '+(buffer.volumeUnit==='shares'?'Khối lượng CP':'Khối lượng nguồn SSI')+' · Đồng hồ nhận');
    setText('notice',buffer.units==='EMPIRICALLY_VERIFIED'?'Đã đối chiếu đơn vị 3 mức mua/bán của ACB. Sự kiện chỉ được phát hiện khi phiên giao dịch và chất lượng dữ liệu đủ điều kiện. Các mức sâu hơn hiển thị theo cùng schema SSI.':'Giá và khối lượng đang hiển thị theo số nguồn SSI; chưa đối chiếu riêng mã này. Detector tạm khóa để tránh kết luận sai.');
    if(!f){setText('bookState','Chờ dữ liệu');$('book').replaceChildren();renderEvents();return;}
    setText('clock',clock(f.time));setText('clockDelta',fmt(f.clock_delta_ms/1000)+' giây');setText('bookState',f.quality+' · '+f.units);
    setText('quality','Nguồn: '+f.source_time+' · Nhận: '+f.received_at+' · Cờ phiên SSI: '+(f.market_flag||'chưa nhận')+(f.market_flag_source?' ('+f.market_flag_source+')':'')+(f.market_flag_conflict?' · Cờ phiên mâu thuẫn':'')+' · '+(f.issues||[]).join(', '));
    const table=document.createElement('table'),head=document.createElement('tr');for(const label of ['Lượng mua','Giá mua','Giá bán','Lượng bán']){const th=document.createElement('th');th.textContent=label;head.append(th);}table.append(head);
    const bids=f.bids.filter(p=>p[0]!==null),asks=f.asks.filter(p=>p[0]!==null);
    for(let i=0;i<Math.max(bids.length,asks.length);i++){const tr=document.createElement('tr');for(const v of [bids[i]?.[1],bids[i]?.[0],asks[i]?.[0],asks[i]?.[1]]){const td=document.createElement('td');td.textContent=fmt(v);tr.append(td);}table.append(tr);}$('book').replaceChildren(table);
    renderEvents();paint();
  }
  async function poll(){
    try{
      status=await api('/api/live/status');csrf=status.csrf;
      setText('badge',(states[status.state]||status.state)+(status.stale?' · Dữ liệu cũ':''));
      setText('feed',(status.reason||'')+' · Kết nối #'+status.connection+' · '+(follow?'Theo dõi':'Màn hình tạm dừng'));
      const selected=runs.find(r=>r.run_id===$('run').value),current=status.run_id===$('run').value,symbol=$('symbol').value;
      setText('quoteCount',fmt(current?(status.quote_counts?.[symbol]??status.quotes):(selected?.quote_counts?.[symbol]??buffer.total)));
      setText('tradeCount',fmt(current?(status.trade_counts?.[symbol]??status.trades):(selected?.trade_counts?.[symbol]??buffer.trades.length)));setText('gapCount',fmt(status.gaps));
      const age=status.quote_ages?.[symbol]??status.quote_age_seconds;
      setText('age',!current||!status.active?'Phiên đã lưu':age===null?'Chưa nhận sổ lệnh':fmt(age)+' giây từ sổ lệnh mới nhất của mã');
      setText('persisted','Parquet: '+fmt(status.persisted_records)+' bản ghi · Journal: '+fmt(status.pending_records)+' · Lỗi dữ liệu: '+fmt(status.rejected));
      $('start').disabled=status.active||status.hosted;$('stop').disabled=!status.active||status.hosted;
      if(status.hosted){$('start').textContent='Thu tự động theo lịch GitHub';$('stop').hidden=true;}
      if(!runs.length||status.run_id&&!runs.some(r=>r.run_id===status.run_id))await refreshRuns();
      if(follow&&$('run').value){
        if(buffer.run!==$('run').value||buffer.symbol!==$('symbol').value)await load();
        else if(status.run_id===buffer.run){const after=buffer.frames.at(-1)?.index??-1,afterTrade=buffer.trades.at(-1)?.capture_ordinal??-1;const p=await api('/api/live/view?'+query()+'&after='+after+'&after_trade='+afterTrade);buffer.accept(p);index=Math.max(0,buffer.frames.length-1);render();await events();}
      }
    }catch(error){fail(error);}finally{setTimeout(poll,1000);}
  }
  $('start').onclick=async()=>{try{$('start').disabled=true;$('error').hidden=true;status=await api('/api/live/start',{symbols:['ACB','FPT','HPG'],seconds:21600});await refreshRuns();$('run').value=status.run_id;follow=true;await load();}catch(e){fail(e);$('start').disabled=false;}};
  $('stop').onclick=async()=>{try{await api('/api/live/stop',{});setText('feed','Đang đóng kết nối và lưu Parquet…');}catch(e){fail(e);}};
  $('pause').onclick=()=>{follow=false;playing=false;render();events().catch(fail);};
  $('live').onclick=async()=>{try{playing=false;follow=true;if(status.run_id){await refreshRuns();$('run').value=status.run_id;}await load();}catch(e){fail(e);}};
  $('cursor').oninput=()=>{follow=false;playing=false;index=Number($('cursor').value);buffer.events=[];$('review').hidden=true;$('evidence').textContent='Đang đọc bằng chứng tại con trỏ…';render();events().catch(fail);};
  $('play').onclick=()=>{if(!buffer.frames.length)return;follow=false;playing=!playing;playWall=performance.now();playOrigin=M.time(buffer.frames[index].time);$('play').textContent=playing?'Ⅱ Dừng phát':'▶ Phát lại';};
  function tick(now){if(playing&&buffer.frames.length&&now-lastPaint>=200){lastPaint=now;const previous=index,stop=playOrigin+(now-playWall)*Number($('speed').value);while(index<buffer.frames.length-1&&M.time(buffer.frames[index+1].time)<=stop)index++;if(index!==previous){buffer.events=[];render();events().catch(fail);}if(index===buffer.frames.length-1){playing=false;$('play').textContent='▶ Phát lại';}}requestAnimationFrame(tick);}
  $('speed').onchange=()=>{if(buffer.frames[index]){playOrigin=M.time(buffer.frames[index].time);playWall=performance.now();}};
  for(const id of ['run','symbol'])$(id).onchange=()=>{follow=false;playing=false;load().catch(fail);};
  $('older').onclick=()=>{follow=false;load(Math.max(0,(buffer.frames[0]?.index||0)-5000)).catch(fail);};
  $('newer').onclick=()=>{follow=false;load(Math.min(Math.max(0,buffer.total-1),(buffer.frames.at(-1)?.index||0)+1)).catch(fail);};
  for(const id of ['window','scale','bubbles','group'])$(id).onchange=paint;
  for(const button of document.querySelectorAll('[data-side]'))button.onclick=()=>{side=button.dataset.side;for(const b of document.querySelectorAll('[data-side]'))b.classList.toggle('selected',b===button);paint();};
  $('heatmap').onmousemove=e=>{const rect=$('heatmap').getBoundingClientRect();const hit=layout?.hits.find(h=>Math.hypot(e.clientX-rect.left-h.x,e.clientY-rect.top-h.y)<=h.radius+3);$('hover').hidden=!hit;if(hit)setText('hover','KHỚP LỆNH SSI · '+hit.bubble.side+'\nGiá '+fmt(hit.bubble.price)+' · Lượng '+fmt(hit.bubble.qty)+'\n'+fmt(hit.bubble.count)+' giao dịch · '+clock(hit.bubble.first)+' – '+clock(hit.bubble.last));};$('heatmap').onmouseleave=()=>{$('hover').hidden=true;};
  $('review').onsubmit=async e=>{e.preventDefault();if(!eventId)return;const run=buffer.run,id=eventId,symbol=buffer.symbol;try{await api('/api/live/reviews',{run_id:run,symbol,event_id:id,verdict:$('verdict').value,note:$('note').value});const rows=await api('/api/live/reviews?run_id='+run+'&symbol='+symbol);if(buffer.run===run&&eventId===id){noteRows=rows.filter(r=>r.event_id===id);setText('reviewState',(status.hosted?'Đã lưu nhận xét trên máy chủ; giữ toàn bộ lịch sử.':'Đã lưu nhận xét trên máy; giữ toàn bộ lịch sử.'));renderEvents();}}catch(error){fail(error);}};
  new ResizeObserver(paint).observe($('heatmap').parentElement);requestAnimationFrame(tick);
  poll();
})();
