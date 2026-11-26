'use strict';
(() => {
  const $=id=>document.getElementById(id),M=EventReplay;
  const fmt=(v,n=0)=>v===null||v===undefined||!Number.isFinite(Number(v))?'—':new Intl.NumberFormat('vi-VN',{maximumFractionDigits:n}).format(Number(v));
  const pct=v=>fmt(Number(v)*100,1)+'%';
  const clock=t=>new Date(M.time(t)+7*3600000).toISOString().slice(11,23);
  const local=t=>new Date(M.time(t)+7*3600000).toISOString().replace('T',' ').slice(0,23)+' +07';
  const dayLabel=d=>d.split('-').reverse().join('/');
  const names={SPREAD_WIDENING:'Spread mở rộng',DEPTH_DROP:'Depth giảm',IMBALANCE_PERSISTENCE:'Imbalance kéo dài'};
  const states={INHIBITED:'Chưa đủ điều kiện',WARMING_UP:'Đang tích lũy baseline',NORMAL:'Đang quan sát',CANDIDATE:'Đang kiểm tra duy trì',ACTIVE:'Đang diễn ra',RECOVERING:'Đang kiểm tra hồi phục',COOLDOWN:'Khoảng nghỉ',ENDED:'Đã kết thúc',INTERRUPTED:'Chuỗi quan sát bị ngắt'};
  const reasons={OUTSIDE_CONTINUOUS_SESSION:'Ngoài phiên liên tục',OBSERVATION_GAP:'Khoảng quan sát ngắt',SCOPE_OR_SESSION_CHANGED:'Chuyển phiên / phạm vi',MISSING_OR_INVALID_BEST_QUOTES:'Thiếu giá mua/bán hợp lệ',UNVERIFIED_UNITS:'Thiếu căn cứ về đơn vị',INVALID_OBSERVATION:'Quan sát không hợp lệ',INCOMPLETE_DEPTH:'Thiếu mức giá/khối lượng',NO_PREVIOUS_COMPARABLE_BOOK:'Chưa có book trước để so sánh',INSUFFICIENT_COMMON_PRICE_COVERAGE:'Không đủ mức giá cùng còn quan sát',IMBALANCE_DIRECTION_CHANGED:'Imbalance đổi phía',INSUFFICIENT_TOTAL_DEPTH:'Depth dưới ngưỡng tối thiểu',RETURNED_BELOW_RELEASE_THRESHOLD:'Về dưới ngưỡng hồi phục đủ lâu',NO_DATA:'Chưa có snapshot trước mốc',STALE_AT_CUTOFF:'Snapshot cách mốc xem quá 60 giây',VERIFIED:'Đủ đầu vào phân tích',OUT_OF_ORDER_SOURCE_TIME:'Thời gian nguồn sai thứ tự'};
  const verdicts={unreviewed:'Chưa rà soát',useful:'Hữu ích',not_useful:'Không hữu ích',data_issue:'Vấn đề dữ liệu',uncertain:'Chưa đủ thông tin'};
  Object.assign(reasons,{RELATIVE_ANALYSIS_ONLY:'Chỉ phân tích theo tỷ lệ',ABSOLUTE_VOLUME_REQUIRES_VERIFIED_UNITS:'Chưa đủ điều kiện dùng ngưỡng khối lượng tuyệt đối',INCOMPLETE_RELATIVE_BOOK:'Thiếu ba mức mua/bán hợp lệ',INVALID_RELATIVE_LEVEL_ORDER:'Mức giá không đúng thứ tự',RELATIVE_SCHEMA_NOT_SUPPORTED:'Schema chưa hỗ trợ phân tích tương đối'});
  const relative=()=>data?.detector_scope==='RELATIVE_RATIOS_ONLY_UNVERIFIED_UNITS';
  const observationOnly=()=>data?.detector_scope==='OBSERVATION_ONLY_NO_NEW_UNIT_VERIFICATION';
  let data=null,index=0,selected=null,side='both',page='analysis',csrf='',catalog=[],notes=[],library=[],generation=0,watchGeneration=0;
  let playing=false,origin=0,wall=0,lastPaint=0,allEvents=[],marketPage=0;
  let tradePack=null,tradeError='',chartLayout=null,hosted=false,readOnly=false;
  let mode='history',capture=new MonitorCapture.Replay(),captureStatus={},captureRuns=[],following=false,captureEpoch=0,captureEvents=0,appliedCaptureEvents=0,captureEventCutoff=null,captureTimer=null,eventTimer=null;
  const isCapture=()=>mode==='capture';
  const eventsAt=(d,i)=>isCapture()?capture.eventsAt(i):M.eventsAt(d,i);
  const text=(tag,value='',cls)=>{const e=document.createElement(tag);e.textContent=value;if(cls)e.className=cls;return e;};
  const button=(label,fn,cls='outline')=>{const b=text('button',label,cls);b.type='button';b.addEventListener('click',fn);return b;};
  function showError(message){$('error').textContent=message;$('error').hidden=false;}
  const http=MonitorHTTP.create({fetch:(path,options)=>fetch(path,options),onWaiting:state=>{
    $('serverNotice').hidden=!state;if(!state)return;
    $('serverNotice').textContent=state.code==='DATA_RESTORING_RETRY'?'Máy chủ đang phục hồi dữ liệu đã lưu. Trang đang chờ và sẽ tự tải tiếp; không cần bấm tải lại.':state.code==='SERVER_BUSY_RETRY'?'Máy chủ đang xử lý yêu cầu dữ liệu khác. Trang đang chờ đến lượt và sẽ tự thử lại.':'Đang kết nối lại máy chủ. Dữ liệu đã tải được giữ nguyên.';
    if(isCapture())captureState();
  }});
  const api=(path,options={})=>http.request(path,options);
  const breakoutUI=typeof MonitorBreakout==='undefined'?null:MonitorBreakout.create({document,api,
    context:()=>({data,capture:isCapture(),cutoff:data?.frames[index]?.time}),
    onEnd:()=>{if(data){stop();setIndex(data.frames.length-1);}},
    onSeek:t=>{if(data){stop();setIndex(M.indexAt(data,M.time(t)));$('heatmap').scrollIntoView({behavior:'smooth',block:'center'});}}});
  function playbackLabel(){
    $('play').textContent=playing?'Ⅱ Tạm dừng':'▶ Phát lại';
    $('play').title=(playing?'Tạm dừng':'Phát lại')+' (Space)';
  }
  function stop(){playing=false;playbackLabel();}
  function togglePlayback(){
    if(!data||$('play').disabled)return;
    if(playing){stop();return;}
    if(index===data.frames.length-1)setIndex(0);
    if(isCapture()&&following){
      following=false;captureEpoch++;captureEvents++;captureState();
    }
    playing=true;
    origin=M.time(data.frames[index].time);
    wall=performance.now();
    playbackLabel();
  }
  function table(headers,rows){const t=document.createElement('table'),h=document.createElement('thead'),tr=document.createElement('tr');for(const v of headers)tr.append(text('th',v));h.append(tr);t.append(h);const body=document.createElement('tbody');for(const values of rows){const r=document.createElement('tr');for(const v of values){const td=document.createElement('td');if(typeof v==='object'&&v!==null)td.append(v);else td.textContent=v;r.append(td);}body.append(r);}t.append(body);return t;}
  function detail(box,label,value){const r=text('div','','detail-row');r.append(text('span',label),text('strong',String(value)));box.append(r);}
  function title(e){return (names[e.type]||e.type)+(e.type==='DEPTH_DROP'?' · phía '+(e.side==='bid'?'mua':'bán'):'');}
  function modeControls(){
    breakoutUI?.sync();
    $('dataMode').value=mode;for(const id of ['captureRunLabel','captureActions','captureTools'])$(id).hidden=!isCapture();
    for(const id of ['marketExchange','marketSearch'])$(id).disabled=isCapture();
    $('marketFilters').hidden=isCapture();
    $('modeStatus').hidden=!isCapture();
    $('openConfig').disabled=isCapture()||readOnly;
    for(const id of ['libraryFilters','archiveToggle'])$(id).hidden=isCapture();
    $('replayScope').textContent=isCapture()?'Đang xem một đoạn bản thu theo đồng hồ nhận. Tua/tạm dừng chỉ đổi màn hình; bộ thu vẫn lưu. Dùng Đoạn trước/Đoạn sau để tải phần khác.':'Sự kiện, peak và trạng thái kết thúc sau con trỏ được ẩn. Thư viện và xuất hồ sơ là xem hồi tố cả phiên.';
    if(!isCapture()){$('viewBadge').textContent='LỊCH SỬ · ĐÃ LƯU';$('modeStatus').textContent='Dữ liệu đã lưu · Chọn ngày và mã để xem lại.';}
  }
  function captureOptions(symbol,day,run){
    const days=[...new Set(captureRuns.map(r=>r.day))].sort().reverse(),chosenDay=days.includes(day)?day:days[0];
    setOptions('daySelect',days.map(d=>[d,dayLabel(d)]),chosenDay);$('daySelect').disabled=!days.length;
    const runs=captureRuns.filter(r=>r.day===chosenDay);
    setOptions('captureRun',runs.map(r=>[r.run_id,clock(r.created_at).slice(0,8)+' · '+(r.closed_at?'Đã lưu':'Bản thu')]),runs.some(r=>r.run_id===run)?run:runs[0]?.run_id);
    const selected=captureRuns.find(r=>r.run_id===$('captureRun').value),symbols=selected?.symbols||[];
    const choices=[...new Set([symbol,...symbols].filter(Boolean))];
    setOptions('symbolSelect',choices.map(s=>[s,s+(symbols.includes(s)?'':' · Chưa thu')]),symbol||symbols[0]);$('symbolSelect').disabled=!choices.length;
  }
  function captureState(){
    if(!isCapture())return;
    const label=capture.label(captureStatus,index,following);$('viewBadge').textContent=label;
    const active=capture.meta?.run_id===captureStatus.run_id;
    const quoteAge=captureStatus.quote_ages?.[data?.symbol]??captureStatus.quote_age_seconds;
    const q=active?captureStatus.quote_counts?.[data?.symbol]:captureRuns.find(r=>r.run_id===capture.meta?.run_id)?.quote_counts?.[data?.symbol];
    $('modeStatus').textContent=label+' · '+fmt(q)+' quan sát đã thu của mã'+(active?' · '+fmt(captureStatus.gaps)+' khoảng kết nối ngắt':'')+(active&&quoteAge!==null&&quoteAge!==undefined?' · Bản tin gần nhất '+fmt(quoteAge)+' giây trước':'')+'. Tạm dừng màn hình không dừng bộ thu.';
    $('pauseCapture').disabled=!following;$('followLatest').disabled=following&&active;
    $('captureEarlier').disabled=!data?.frames.length||data.frames[0].index===0;
    $('captureLater').disabled=!data?.frames.length||data.frames.at(-1).index>=capture.window.total-1;
    $('connection').textContent=label;
  }
  function emptyCapture(message){
    data=null;tradePack=null;selected=null;notes=[];allEvents=[];stop();
    breakoutUI?.sync();
    for(const id of ['spread','bidDepth','askDepth','clock','sourceClock','position'])$(id).textContent='—';
    $('play').disabled=true;$('cursor').disabled=true;$('eventCount').textContent='—';$('reviewForm').hidden=true;
    for(const id of ['events','watchEvents','evidence','comparison','quality','watchQuality'])$(id).replaceChildren();
    $('heatmap').width=$('heatmap').width;$('timeline').width=$('timeline').width;
    $('book').textContent=message;$('provenance').textContent=message;$('bubbleStatus').textContent='Chưa có giao dịch trong đoạn đang xem; không suy ra khối lượng bằng 0.';
    $('bookQuality').textContent='CHỜ DỮ LIỆU';$('bookUnits').textContent='';$('detectorState').textContent='Chưa có đầu vào';$('filterCount').textContent='Chưa có sự kiện để đánh giá';$('unitWarning').hidden=true;
    for(const id of ['watchTable','inventory','libraryTable'])$(id).replaceChildren();$('watchStatus').textContent=message;
    $('exportCase').hidden=true;$('exportCSV').hidden=true;$('exportJSON').hidden=true;
    captureState();
  }
  async function captureEventQuery(){
    if(!isCapture()||!data?.frames.length)return;
    const ticket=++captureEvents,epoch=captureEpoch,at=data.frames[index].time,run=capture.meta.run_id,symbol=data.symbol;
    const rows=await api('/api/live/events?run_id='+run+'&symbol='+symbol+'&at='+encodeURIComponent(at));
    if(!isCapture()||epoch!==captureEpoch||ticket<appliedCaptureEvents||!data?.frames[index]||M.time(at)>M.time(data.frames[index].time))return;
    appliedCaptureEvents=ticket;captureEventCutoff=at;capture.setEvents(rows,at);allEvents=capture.eventsAt(index);render();
    if(page==='review')renderCaptureLibrary();
  }
  function queueCaptureEvents(){
    if(!isCapture())return;capture.window.events=[];captureEventCutoff=null;if(eventTimer!==null)return;
    eventTimer=setTimeout(()=>{eventTimer=null;return captureEventQuery().catch(e=>{if(isCapture())showError(e.message);});},250);
  }
  async function loadCapture(start=null,{delta=false}={}){
    if(!isCapture())return;
    const epoch=++captureEpoch;captureEvents++;captureEventCutoff=null;stop();
    const run=$('captureRun').value,symbol=$('symbolSelect').value,entry=captureRuns.find(r=>r.run_id===run);
    $('error').hidden=true;
    if(!entry||!entry.symbols.includes(symbol)){capture=new MonitorCapture.Replay();emptyCapture(symbol+' chưa có bản thu trong lựa chọn này. Chọn mã khác hoặc chuyển sang Lịch sử.');renderSide();return;}
    const same=capture.meta?.run_id===run&&capture.meta?.symbol===symbol;
    if(!same||!delta){capture=new MonitorCapture.Replay();emptyCapture('Đang tải bản thu '+symbol+'…');}
    const after=delta&&same?(capture.window.frames.at(-1)?.index??-1):-1;
    const afterTrade=delta&&same?(capture.window.trades.at(-1)?.capture_ordinal??-1):-1;
    const url='/api/live/view?run_id='+run+'&symbol='+symbol+(start!==null?'&start='+start:'&after='+after+'&after_trade='+afterTrade);
    const packet=await api(url);if(!isCapture()||epoch!==captureEpoch)return;
    data=capture.accept(packet);tradePack={trades:capture.window.trades,unit_status:capture.window.units};tradeError='';notes=[];
    if(!data.frames.length){emptyCapture('Chưa có sổ lệnh cho '+symbol+' trong bản thu này. Màn hình sẽ cập nhật khi nhận bản tin.');renderSide();return;}
    index=data.frames.length-1;selected=null;$('reviewForm').dataset.event='';
    $('play').disabled=false;$('cursor').disabled=false;$('openConfig').disabled=true;
    $('cursor').max=data.frames.length-1;$('cursor').value=index;
    const verified=capture.window.units==='EMPIRICALLY_VERIFIED';$('unitWarning').hidden=verified;
    $('unitWarning').textContent='Đơn vị của mã này chưa được đối chiếu trong phạm vi bản thu. Hiển thị số nguồn; detector vẫn theo điều kiện chất lượng và bằng chứng hiện có.';
    $('provenance').textContent=symbol+' · '+dayLabel(data.day)+' · Đồng hồ nhận · Đoạn '+fmt(data.frames[0].index+1)+'–'+fmt(data.frames.at(-1).index+1)+' / '+fmt(packet.total)+' quan sát đã thu';
    $('exportJSON').hidden=false;$('exportCSV').hidden=true;
    if(!readOnly){const rows=await api('/api/live/reviews?run_id='+run+'&symbol='+symbol);if(!isCapture()||epoch!==captureEpoch)return;notes=rows;}
    renderSide();setPage(page);render();captureState();await captureEventQuery();
  }
  async function pollCapture(epoch){
    if(!isCapture()||epoch!==pollEpoch)return;
    try{
      captureStatus=await api('/api/live/status');if(!isCapture()||epoch!==pollEpoch)return;
      csrf=captureStatus.csrf;readOnly=Boolean(captureStatus.read_only);
      if(following){
        if(captureStatus.run_id&&$('captureRun').value!==captureStatus.run_id){
          if(!captureRuns.some(r=>r.run_id===captureStatus.run_id))captureRuns=await api('/api/live/runs');if(!isCapture()||epoch!==pollEpoch)return;
          captureOptions($('symbolSelect').value,captureRuns.find(r=>r.run_id===captureStatus.run_id)?.day,captureStatus.run_id);
        }
        const current=$('captureRun').value===captureStatus.run_id,total=captureStatus.quote_counts?.[$('symbolSelect').value];
        if((!data&&current)||current&&(captureStatus.active||Number.isSafeInteger(total)&&total>capture.window.total))await loadCapture(null,{delta:true});
      }
      captureState();
    }catch(e){if(isCapture()&&epoch===pollEpoch){$('modeStatus').textContent='Không cập nhật được bản thu. Dữ liệu đang xem có thể đã cũ.';showError(e.message);$('viewBadge').textContent='MẤT CẬP NHẬT';}}
    finally{if(isCapture()&&epoch===pollEpoch)captureTimer=setTimeout(()=>pollCapture(epoch),3000);}
  }
  let pollEpoch=0;
  async function switchMode(next){
    if(!['history','capture'].includes(next))return;
    const wanted=$('symbolSelect').value||data?.symbol,previousSymbol=/^[A-Z][A-Z0-9]{1,11}$/.test(wanted)?wanted:'ACB',previousDay=$('daySelect').value;
    mode=next;generation++;watchGeneration++;captureEpoch++;captureEvents++;const epoch=++pollEpoch;
    clearTimeout(captureTimer);clearTimeout(eventTimer);eventTimer=null;following=false;stop();data=null;modeControls();$('error').hidden=true;emptyCapture('Đang tải dữ liệu…');
    try{
      if(isCapture()){
        const [status,runs]=await Promise.all([api('/api/live/status'),api('/api/live/runs')]);
        if(!isCapture()||epoch!==pollEpoch)return;
        captureStatus=status;captureRuns=runs;csrf=status.csrf;readOnly=Boolean(status.read_only);
        const run=runs.find(r=>r.run_id===status.run_id)||runs[0];following=true;
        captureOptions(previousSymbol,run?.day,run?.run_id);await loadCapture();
        if(isCapture()&&epoch===pollEpoch)captureTimer=setTimeout(()=>pollCapture(epoch),3000);
      }else{
        await refreshCatalog();if(isCapture()||epoch!==pollEpoch)return;
        const entries=currentEntries(),entry=entries.find(e=>e.day===previousDay&&e.symbol===previousSymbol&&e.observations)||[...entries].reverse().find(e=>e.symbol===previousSymbol&&e.observations)||entries.find(e=>e.observations);
        if(entry)await load(entry.dataset_id);else showError('Chưa có lịch sử phù hợp. Có thể chuyển sang Theo dõi gần nhất.');
      }
    }catch(e){if(epoch===pollEpoch){showError(e.message);if(isCapture())emptyCapture('Không tải được bản thu. Thử chọn lại chế độ.');}}
  }
  function currentEntries(){return catalog.filter(e=>!e.archived);}
  function setOptions(id,options,value){const el=$(id);el.replaceChildren();for(const [v,label] of options){const o=text('option',label);o.value=v;el.append(o);}el.value=value;}
  function setPage(next){const changed=page!==next;page=next;if(next!=='analysis')stop();for(const p of ['watch','analysis','review'])$(p+'Screen').hidden=p!==next;
    for(const b of document.querySelectorAll('[data-page]')){
      const active=b.dataset.page===next;
      b.classList.toggle('active',active);
      b.setAttribute('aria-current',active?'page':'false');
    }
    const labels={watch:['Theo dõi','Danh sách theo dõi','Xem trạng thái các mã tại cùng một mốc nguồn.'],analysis:['Phân tích mã','Phân tích sổ lệnh','Heatmap, replay và bằng chứng của từng sự kiện.'],review:['Rà soát & ghi chú','Thư viện sự kiện','Tìm lại diễn biến, xem nhận xét và xuất hồ sơ hồi tố.']};
    $('pageLabel').textContent=labels[next][0];$('title').textContent=labels[next][1]+(next==='analysis'&&data?' '+data.symbol:'');$('pageDescription').textContent=labels[next][2];
    if(changed)window.scrollTo?.(0,0);
    if(next==='watch'&&data){updateWatch();renderInventory();}if(next==='review')loadLibrary();if(next==='analysis')paint();
  }
  async function refreshCatalog(){const epoch=pollEpoch,r=await api('/api/datasets');if(isCapture()||epoch!==pollEpoch)return;if(!['offline-monitor-v3-bubbles','offline-monitor-v4-parquet','offline-monitor-v5-market','offline-monitor-v6-relative','offline-monitor-v7-public','offline-monitor-v8-unified','offline-monitor-v9-recovery','offline-monitor-v10-bounded','offline-monitor-v11-breakout','offline-monitor-v12-streamed-cutoff'].includes(r.application_version))throw Error('Giao diện và server chưa đồng bộ phiên bản. Hãy tải lại trang sau khi cập nhật ứng dụng.');csrf=r.csrf;readOnly=Boolean(r.read_only);catalog=r.datasets;
    const days=[...new Set(currentEntries().map(e=>e.day))].sort().reverse();if(!days.length)throw Error('Chưa có bộ dữ liệu lịch sử để hiển thị.');
    setOptions('daySelect',days.map(d=>[d,dayLabel(d)]),data?.day||days[0]);
    setOptions('libraryDay',[['all','Tất cả'],...days.map(d=>[d,dayLabel(d)])],$('libraryDay').value||'all');
    setOptions('librarySymbol',[['all','Tất cả'],...[...new Set(currentEntries().map(e=>e.symbol))].sort().map(s=>[s,s])],$('librarySymbol').value||'all');
    $('datasetInventory').textContent=`${days.length} ngày · ${new Set(currentEntries().map(e=>e.symbol)).size} mã · lịch sử đã lưu`;
    $('daySelect').disabled=false;$('symbolSelect').disabled=false;renderSide();renderInventory();
  }
  function renderSide(){if(isCapture())return renderCaptureSide();const box=$('sideWatch');box.replaceChildren();for(const symbol of [...new Set(['ACB','FPT','HPG',data?.symbol].filter(Boolean))]){const e=currentEntries().find(x=>x.day===$('daySelect').value&&x.symbol===symbol);const b=button('',async()=>{if(e?.observations)await load(e.dataset_id,{page:'analysis'});},'watch'+(data?.symbol===symbol?' active':''));b.disabled=!e?.observations;b.append(text('strong',symbol));if(!e?.observations)b.append(text('small','Chưa có dữ liệu'));box.append(b);}}
  function marketEntries(){const q=$('marketSearch').value.trim().toUpperCase(),exchange=$('marketExchange').value;return currentEntries().filter(e=>e.day===$('daySelect').value&&(!q||e.symbol.includes(q))&&(!exchange||exchange==='all'||(e.exchange||'HOSE')===exchange)).sort((a,b)=>a.symbol.localeCompare(b.symbol));}
  function marketRows(){return marketEntries().slice(marketPage*50,marketPage*50+50);}
  function symbolOptions(selected){if(isCapture())return captureOptions(selected,$('daySelect').value,$('captureRun').value);const choices=marketEntries().filter(e=>e.observations);setOptions('symbolSelect',choices.map(e=>[e.symbol,e.symbol+' · '+(e.exchange||'HOSE')]),choices.some(e=>e.symbol===selected)?selected:choices[0]?.symbol);}
  function renderInventory(){if(isCapture())return;const all=marketEntries();marketPage=Math.min(marketPage,Math.max(0,Math.ceil(all.length/50)-1));const entries=marketRows();$('inventory').replaceChildren(table(['Ngày','Mã / Sàn','Quan sát đã lưu','Sự kiện cả phiên','Phạm vi / dữ liệu',''],entries.map(e=>[dayLabel(e.day),e.symbol+' · '+(e.exchange||'HOSE'),fmt(e.observations),e.market_observation?'Tính khi mở mã':fmt(Object.values(e.event_counts||{}).reduce((a,b)=>a+b,0)),!e.observations?'Nguồn không trả quan sát · không suy ra khối lượng 0':e.market_observation?'Phân tích theo tỷ lệ khi đủ dữ liệu':'Đã tính sự kiện',e.observations?button('Mở',()=>load(e.dataset_id,{page:'analysis'})):'Chưa có sổ lệnh'])));$('marketPageLabel').textContent=`${all.length} mã khớp · ${all.filter(e=>e.observations).length} có quan sát · trang ${marketPage+1}/${Math.max(1,Math.ceil(all.length/50))}`;$('marketPrev').disabled=marketPage===0;$('marketNext').disabled=(marketPage+1)*50>=all.length;}
  async function choose(){if(isCapture()){following=false;captureOptions($('symbolSelect').value,$('daySelect').value,$('captureRun').value);await loadCapture();return;}const symbol=$('symbolSelect').value||data?.symbol||'ACB';symbolOptions(symbol);renderSide();renderInventory();const entry=currentEntries().find(e=>e.day===$('daySelect').value&&e.symbol===$('symbolSelect').value);if(entry?.observations)await load(entry.dataset_id);else showError('Bộ lọc không có mã với quan sát đã lưu. Đổi mã hoặc sàn; không coi phần thiếu là khối lượng 0.');}
  async function load(id,options={}){const token=++generation;stop();data=null;breakoutUI?.sync();tradePack=null;tradeError='';chartLayout=null;selected=options.eventId||null;$('error').hidden=true;$('play').disabled=true;$('cursor').disabled=true;$('reviewForm').hidden=true;$('connection').textContent='Đang đọc dữ liệu…';$('bubbleStatus').textContent='Đang đọc dữ liệu khớp…';
    if(isCapture()){mode='history';pollEpoch++;captureEpoch++;captureEvents++;clearTimeout(captureTimer);clearTimeout(eventTimer);modeControls();}
    for(const n of ['spread','bidDepth','askDepth','clock','sourceClock','position'])$(n).textContent='—';$('eventCount').textContent='0';$('events').replaceChildren();$('evidence').replaceChildren();$('book').textContent='Đang tải…';$('heatmap').width=$('heatmap').width;
    try{const pack=await api('/api/dataset?dataset_id='+encodeURIComponent(id));if(token!==generation)return;const [review,trades]=await Promise.all([api('/api/reviews?dataset_id='+encodeURIComponent(id)),api('/api/trades?dataset_id='+encodeURIComponent(id)).catch(e=>({error:e.message}))]);if(token!==generation)return;
      data=M.validate(pack);notes=review;index=options.targetTime?M.indexAt(data,M.time(options.targetTime)):Math.max(0,Math.min(data.frames.length-1,options.index||0));allEvents=eventsAt(data,data.frames.length-1);eventsAt(data,index);
      try{if(trades.error)throw Error(trades.error);tradePack=M.validateTrades(trades,data);}catch(e){tradeError=e.message;}
      $('daySelect').value=data.day;const entry=catalog.find(e=>e.dataset_id===id);if(!marketEntries().some(e=>e.symbol===data.symbol)){$('marketSearch').value='';$('marketExchange').value='all';}symbolOptions(data.symbol);$('openConfig').disabled=readOnly||Boolean(entry?.market_observation);
      $('cursor').max=data.frames.length-1;$('cursor').disabled=false;$('play').disabled=false;$('reviewForm').dataset.event='';
      const archived=catalog.find(e=>e.dataset_id===id)?.archived;
      $('provenance').textContent=`${data.symbol} · ${dayLabel(data.day)} · ${fmt(data.frames.length)} quan sát đầy đủ · ${archived?'PHIÊN BẢN CŨ · ':''}Thời gian nguồn hồi tố · Ngưỡng thử nghiệm`;
      const verified=data.frames.some(f=>['EMPIRICALLY_VERIFIED','OFFICIALLY_VERIFIED'].includes(f.units));$('unitWarning').hidden=verified;$('unitWarning').textContent=relative()?'Phân tích tương đối thử nghiệm: D1 spread theo bps và D2 lượng chờ giảm theo tỷ lệ tại cùng giá. Đơn vị tuyệt đối chưa được xác minh; D3 và tính tiền vẫn bị chặn. Không suy ra khớp/hủy lệnh hoặc thị trường bình thường từ các kết quả này.':'Mã này chưa xác minh độc lập đơn vị. Đang chỉ quan sát; có thể thiếu đầu vào hoặc vượt giới hạn phân tích. Không có sự kiện không đồng nghĩa thị trường bình thường.';
      const p=data.analysis_parameters||{spread_multiplier:'1.5',min_increase_bps:'2',depth_drop:'.5',imbalance_threshold:'.8',duration_seconds:3};$('cfgMultiplier').value=p.spread_multiplier;$('cfgIncrease').value=p.min_increase_bps;$('cfgDepth').value=Number(p.depth_drop)*100;$('cfgImbalance').value=Number(p.imbalance_threshold)*100;$('cfgDuration').value=p.duration_seconds;
      $('configDetails').replaceChildren(text('pre',JSON.stringify({version:data.detector_version,hash:data.config_hash,parameters:p,config:data.detector_configs||data.config},null,2)));
      $('connection').textContent=hosted?'Replay · Dữ liệu đã lưu trên web':'Replay · Dữ liệu trên máy';renderSide();renderInventory();setPage(options.page||page);setIndex(index);
    }catch(error){if(token===generation){showError(error.message);$('connection').textContent='Chưa tải được dữ liệu';}}
  }
  function setIndex(value){if(!data)return;if(isCapture()){if(following){following=false;captureEpoch++;}queueCaptureEvents();}index=Math.max(0,Math.min(data.frames.length-1,Math.trunc(Number(value))));$('cursor').value=index;render();}
  function filtered(events){return M.filterEvents(events,{type:$('eventType').value||'all',state:$('eventState').value||'all',review:$('eventVerdict').value||'all',notes});}
  function eventButtons(box,events){box.replaceChildren();if(!events.length){box.append(text('p','Chưa có sự kiện khớp lựa chọn tại mốc này. Có thể đang warmup, chưa đủ dữ liệu hoặc không vượt ngưỡng.','empty-text'));return;}
    for(const e of events){const b=button('',()=>{if(isCapture())return jumpCaptureEvent(e);stop();selected=e.id;setPage('analysis');setIndex(e.detected_index);},'event-card'+(e.id===selected?' selected':''));b.setAttribute('role','listitem');b.append(text('span',clock(e.detected_at).slice(0,8),'event-time'),text('strong',title(e)),text('small',states[e.state]||e.state));box.append(b);}}
  function renderEvidence(events){const e=events.find(x=>x.id===selected),box=$('evidence');box.replaceChildren();$('comparison').replaceChildren();$('exportCase').hidden=!e;
    if(!e){box.append(text('p',selected?'Sự kiện chưa được phát hiện tại con trỏ này. Phát tiếp để xem bằng chứng khi đã có.':'Chọn một sự kiện để mở giải thích và bằng chứng.','empty-text'));$('reviewForm').hidden=true;return;}
    if(isCapture())return renderCaptureEvidence(e,box);
    if(e.type==='SPREAD_WIDENING'){box.append(text('p',`Spread vượt ${fmt(e.threshold_bps,2)} bps đủ số quan sát và thời gian cấu hình. Baseline được khóa từ trước khi đợt bắt đầu.`,'evidence-lead'));detail(box,'Median quá khứ',fmt(e.baseline.median_bps,2)+' bps');detail(box,'Ngưỡng',fmt(e.threshold_bps,2)+' bps');detail(box,'Mẫu baseline cuối',clock(e.baseline.last_source_time));detail(box,'Số giây có mẫu',fmt(e.baseline.sample_seconds));detail(box,'Peak đến con trỏ',fmt(e.peak_spread_bps,2)+' bps');}
    else if(e.type==='DEPTH_DROP'){const v=e.evidence;box.append(text('p',`Lượng chờ ${e.side==='bid'?'mua':'bán'} giảm ${pct(v.drop_fraction)} trên các mức giá cùng còn được quan sát, so với book tham chiếu đã khóa.`,'evidence-lead'));detail(box,'Depth tham chiếu cùng giá',fmt(v.reference_depth)+(relative()?' đơn vị quy đổi':' CP'));detail(box,'Depth đang so sánh',fmt(v.current_depth)+(relative()?' đơn vị quy đổi':' CP'));detail(box,'Ngưỡng giảm',pct(e.threshold));detail(box,'Giá cùng được quan sát',v.common_prices.join(' · '));detail(box,'Giá ra khỏi phạm vi',v.out_of_observation_prices.join(' · ')||'Không');detail(box,'Tham chiếu',clock(e.baseline.source_time));detail(box,'Peak giảm đến con trỏ',pct(e.peak_value));}
    else{const v=e.evidence;box.append(text('p',`Độ lệch khối lượng |I| vượt ${pct(e.threshold)} trên chuỗi quan sát; phía ${v.side==='bid'?'mua':'bán'} chiếm ưu thế. Đây không phải tín hiệu giao dịch.`,'evidence-lead'));detail(box,'Depth mua / bán',fmt(v.bid_depth)+' / '+fmt(v.ask_depth)+' CP');detail(box,'Imbalance có dấu',fmt(v.signed_imbalance,3));detail(box,'Ngưỡng |I|',pct(e.threshold));detail(box,'Peak |I| đến con trỏ',pct(e.peak_value));detail(box,'Loại ngưỡng','Cố định · thử nghiệm');}
    detail(box,'Bắt đầu vượt điều kiện',clock(e.started_at));detail(box,'Đủ điều kiện phát hiện',clock(e.detected_at));detail(box,'Trạng thái đến con trỏ',states[e.state]||e.state);if(e.ended_at)detail(box,'Kết thúc / ngắt tại',clock(e.ended_at));if(e.end_reason)detail(box,'Lý do',reasons[e.end_reason]||e.end_reason);detail(box,'Phiên bản',e.detector_version);detail(box,'Bằng chứng',e.detection_observation_id?.slice(-16)||'—');
    if(relative())box.append(text('p','Sự kiện dựa trên tỷ lệ; các giá/lượng dưới đây theo schema quy đổi chưa xác minh, không phải số tiền hoặc cổ phiếu đã nghiệm thu.','evidence-note'));
    box.append(text('p','Ngưỡng chưa hiệu chỉnh. Sự kiện mô tả quan sát, không chứng minh trạng thái liên tục giữa mọi snapshot hoặc hành vi hủy/khớp/thao túng.','evidence-note'));
    const beforeIndex=e.reference?.index??Math.max(0,e.start_index-1),afterIndex=Math.min(index,e.last_index??e.detected_index),before=data.frames[beforeIndex],after=data.frames[afterIndex];
    $('comparison').append(text('h3',relative()?'Đối chiếu cùng mức giá theo schema':'Đối chiếu theo giá tuyệt đối'));$('comparison').append(text('p',`${clock(before.time)} → ${clock(after.time)} · Ngoài phạm vi khác 0`,'subtle'));
    for(const s of e.type==='DEPTH_DROP'?[e.side==='bid'?'bids':'asks']:['bids','asks']){$('comparison').append(text('h4',s==='bids'?'Phía chờ mua':'Phía chờ bán'));$('comparison').append(table(['Giá','Trước','Sau','Phạm vi'],M.priceComparison(before,after,s).map(v=>[fmt(v.price),fmt(v.before),fmt(v.after),v.status==='COMMON'?'Cùng quan sát':v.status==='OUT_OF_OBSERVATION'?'Ra khỏi top 3':'Mới quan sát'])));}
    $('exportCase').href='/api/export?dataset_id='+data.dataset_id+'&event_id='+e.id;$('exportCase').textContent='Hồ sơ cả episode ↗';$('reviewForm').hidden=readOnly;
    if($('reviewForm').dataset.event!==selected){$('reviewForm').dataset.event=selected;const items=notes.filter(r=>r.event_id===selected),latest=items.at(-1);$('verdict').value=latest?.verdict||'uncertain';$('note').value=latest?.note||'';$('reviewState').textContent=latest?`${items.length} lần ghi nhận · ${new Date(latest.created_at).toLocaleString('vi-VN')}`:'Chưa có nhận xét. Ghi chú lưu tại máy.';
      $('reviewHistory').replaceChildren();for(const r of items.slice().reverse()){$('reviewHistory').append(text('p',`${new Date(r.created_at).toLocaleString('vi-VN')} · ${verdicts[r.verdict]}\n${r.note}`,'review-entry'));}}
  }
  function renderCaptureSide(){const box=$('sideWatch');box.replaceChildren();const entry=captureRuns.find(r=>r.run_id===$('captureRun').value);for(const symbol of entry?.symbols||[]){const b=button('',async()=>{following=false;$('symbolSelect').value=symbol;await loadCapture();},'watch'+(data?.symbol===symbol?' active':''));b.append(text('strong',symbol));box.append(b);}}
  const captureExport=()=>'/api/live/export?run_id='+capture.meta.run_id+'&symbol='+data.symbol+'&at='+encodeURIComponent(data.frames[index].time);
  async function jumpCaptureEvent(event){
    following=false;stop();
    const target=event.capture_index;if(!data.frames.some(f=>f.index===target))await loadCapture(Math.max(0,target-100));
    if(!isCapture()||!data)return;
    const targetIndex=data.frames.findIndex(f=>f.index===target);if(targetIndex<0)return;
    selected=event.id;setPage('analysis');setIndex(targetIndex);
  }
  function renderCaptureEvidence(e,box){
    const raw=e.capture_event,v=raw.evidence||{};
    const description=raw.type==='SPREAD_WIDENING'?'Spread vượt ngưỡng so với baseline quá khứ trên chuỗi bản tin đã nhận.':raw.type==='DEPTH_DROP'?'Lượng chờ giảm tại các mức giá cùng còn được quan sát. Không suy ra đó là hủy lệnh hay đã khớp.':'Độ lệch khối lượng mua/bán duy trì vượt ngưỡng thử nghiệm.';
    box.append(text('p',description,'evidence-lead'));detail(box,'Phát hiện theo đồng hồ nhận',local(raw.detected_received_at));detail(box,'Trạng thái đến con trỏ',states[raw.state]||raw.state);
    if(raw.type==='SPREAD_WIDENING'){detail(box,'Median baseline',fmt(raw.baseline?.median_bps,2)+' bps');detail(box,'Ngưỡng',fmt(raw.threshold_bps,2)+' bps');detail(box,'Peak đến con trỏ',fmt(raw.peak_spread_bps,2)+' bps');}
    else{detail(box,'Ngưỡng',pct(raw.threshold));detail(box,'Peak đến con trỏ',pct(raw.peak_value));if(v.drop_fraction!==undefined)detail(box,'Giảm tại giá chung',pct(v.drop_fraction));if(v.signed_imbalance!==undefined)detail(box,'Imbalance có dấu',fmt(v.signed_imbalance,3));}
    const beforeId=raw.reference?.index??Math.max(0,raw.start_index-1),afterId=raw.last_index??raw.detected_index;
    const before=data.frames.find(f=>f.index===beforeId),after=data.frames.find(f=>f.index===afterId&&M.time(f.time)<=M.time(data.frames[index].time));
    if(before&&after){$('comparison').append(text('h3','Đối chiếu tại cùng mức giá'));for(const s of raw.type==='DEPTH_DROP'?[raw.side==='bid'?'bids':'asks']:['bids','asks']){$('comparison').append(text('h4',s==='bids'?'Phía chờ mua':'Phía chờ bán'));$('comparison').append(table(['Giá','Trước','Sau','Phạm vi'],M.priceComparison(before,after,s).map(r=>[fmt(r.price),fmt(r.before),fmt(r.after),r.status==='COMMON'?'Cùng quan sát':'Thay đổi phạm vi'])));}}
    else $('comparison').append(text('p','Book tham chiếu nằm ngoài đoạn đang tải. Không thay bằng số 0. Dùng Đoạn trước để xem thêm.','subtle'));
    box.append(text('p','Bằng chứng chỉ đến con trỏ nhận hiện tại; chưa chứng nhận độ đầy đủ upstream. Ngưỡng thử nghiệm, không kết luận thao túng.','evidence-note'));
    $('exportCase').href=captureExport();$('exportCase').textContent='Hồ sơ đến con trỏ ↗';$('reviewForm').hidden=readOnly;
    if($('reviewForm').dataset.event!==selected){$('reviewForm').dataset.event=selected;const items=notes.filter(r=>r.event_id===selected),last=items.at(-1);$('verdict').value=last?.verdict||'uncertain';$('note').value=last?.note||'';$('reviewState').textContent=last?fmt(items.length)+' nhận xét đã lưu':'Chưa có nhận xét.';$('reviewHistory').replaceChildren(...items.map(r=>text('p',r.note,'review-entry')));}
  }
  function captureQuality(box){
    const f=data.frames[index],q=M.qualityAt(data,index);box.replaceChildren();detail(box,'Nguồn dữ liệu','SSI · Bản thu sổ lệnh');detail(box,'Đồng hồ hiển thị','Thời gian và thứ tự nhận');
    detail(box,'Thời gian nhận',local(f.time));detail(box,'Thời gian nguồn',f.source_time?local(f.source_time):'Không có');
    detail(box,'Số thứ tự trong bản thu',fmt(f.index+1)+' / '+fmt(capture.window.total));detail(box,'Đủ điều kiện / đoạn đến con trỏ',fmt(q.eligible)+' / '+fmt(q.observations));
    detail(box,'Khoảng ngắt trong cửa sổ',fmt(capture.geometry(index,Number($('window').value)||900,side)?.gaps.length));detail(box,'Độ đầy đủ upstream','Chưa chứng nhận · không có provider sequence');
    for(const [name,state] of Object.entries(f.features.detectors||{}))detail(box,({spread:'D1 spread',depth_bid:'D2 depth mua',depth_ask:'D2 depth bán',imbalance:'D3 imbalance'})[name]||name,(states[state.state]||state.state)+(state.reason?' · '+(reasons[state.reason]||state.reason):''));
  }
  function paintCapture(){
    const g=capture.geometry(index,Number($('window').value)||900,side,following&&captureStatus.active&&captureStatus.run_id===capture.meta.run_id?Date.now():null);if(!g)return;
    const b=capture.window.bubbles(g,Number($('bubbleGroup').value)||1),rows=$('showBubbles').checked?b.rows.filter(r=>r.qty>=Number($('bubbleMin').value||0)):[];
    const verified=capture.window.units==='EMPIRICALLY_VERIFIED'&&capture.window.trades.every(t=>t.unit_status==='EMPIRICALLY_VERIFIED');
    chartLayout=MonitorHeatmap.draw($('heatmap'),g,Number($('scale').value)||250000,rows,Number($('bubbleScale').value)||10000,!verified);MonitorHeatmap.timeline($('timeline'),data,index,eventsAt(data,index));
    $('bubbleStatus').textContent=b.scaleMismatch?'Đã ẩn giao dịch khác thang giá so với sổ lệnh.':fmt(rows.length)+' bubbles trong cửa sổ theo đồng hồ nhận. '+(verified?'':'Màu trung tính khi chưa đủ bằng chứng đơn vị. ')+'Không dùng tổng đoạn đang tải làm tổng khớp cả phiên.';
    $('bubbleFirst').disabled=!capture.window.trades.length;
  }
  function renderCaptureWatch(){
    const f=data.frames[index];$('watchTable').replaceChildren(table(['Mã','Bid1','Ask1','Depth mua','Depth bán','Mốc nhận','Trạng thái'],[[data.symbol,fmt(f.bids[0]?.[0]),fmt(f.asks[0]?.[0]),fmt(f.features.bid_depth),fmt(f.features.ask_depth),clock(f.time),reasons[f.features.reason]||states[f.features.detector_state]||'Chưa đủ điều kiện']]));
    $('watchStatus').textContent='Bản thu đang mở · Bảng chỉ thể hiện mã đã chọn tại con trỏ nhận; không ghép với lịch sử theo đồng hồ nguồn.';
    const entry=captureRuns.find(r=>r.run_id===capture.meta.run_id);$('inventory').replaceChildren(table(['Mã có trong bản thu','Quan sát đã lưu',''],(entry?.symbols||[]).map(symbol=>[symbol,fmt(captureStatus.run_id===entry.run_id?captureStatus.quote_counts?.[symbol]:entry.quote_counts?.[symbol]),button('Mở',async()=>{following=false;$('symbolSelect').value=symbol;await loadCapture();})])));
    $('marketPageLabel').textContent='Danh sách mã của bản thu đã chọn';$('marketPrev').disabled=true;$('marketNext').disabled=true;
  }
  function renderCaptureLibrary(){
    const events=data?.frames.length?eventsAt(data,index):[];
    $('libraryStatus').textContent='Bản thu đang mở · '+events.length+' sự kiện đến con trỏ nhận. Chuyển Lịch sử để xem thư viện cả phiên.';
    $('libraryTable').replaceChildren(table(['Ngày / mã','Sự kiện','Phát hiện (giờ nhận)','Trạng thái',''],events.map(e=>[dayLabel(data.day)+' · '+data.symbol,title(e),clock(e.detected_at),states[e.state]||e.state,button('Xem bằng chứng',()=>jumpCaptureEvent(e))])));
    $('exportCSV').hidden=true;$('exportJSON').hidden=!data?.frames.length;if(data?.frames.length)$('exportJSON').href=captureExport();
  }
  async function saveCaptureReview(){
    const epoch=captureEpoch,run=capture.meta.run_id,symbol=data.symbol,id=selected;$('saveReview').disabled=true;
    try{await api('/api/live/reviews',{method:'POST',headers:{'Content-Type':'application/json','X-Monitor-CSRF':csrf},body:JSON.stringify({run_id:run,symbol,event_id:id,verdict:$('verdict').value,note:$('note').value})});const rows=await api('/api/live/reviews?run_id='+run+'&symbol='+symbol);if(isCapture()&&epoch===captureEpoch){notes=rows;$('reviewForm').dataset.event='';renderEvidence(eventsAt(data,index));$('reviewState').textContent='Đã lưu nhận xét; giữ lịch sử riêng của chủ dự án.';}}catch(e){if(isCapture())$('reviewState').textContent=e.message;}finally{$('saveReview').disabled=false;}
  }
  function quality(box){if(!data)return;if(isCapture())return captureQuality(box);const f=data.frames[index],q=M.qualityAt(data,index);box.replaceChildren();detail(box,'Nguồn dữ liệu','DNSE · Lịch sử sổ lệnh');if(relative())detail(box,'Đầu vào phân tích tỷ lệ',fmt(q.relativeEligible)+' / '+fmt(q.observations));detail(box,'Thời gian nguồn',local(f.time));detail(box,'Tải về thực tế',local(f.received_at));detail(box,'Quan sát / đủ đầu vào',fmt(q.observations)+' / '+fmt(q.eligible));detail(box,'Khoảng ngắt cùng phiên',fmt(q.gaps));detail(box,'Độ đầy đủ upstream','Chưa chứng nhận · không có provider sequence');
    const detectors=f.features.detectors||{spread:{state:f.features.detector_state,reason:f.features.reason}};for(const [k,v] of Object.entries(detectors))detail(box,({spread:'D1 spread',depth_bid:'D2 depth mua',depth_ask:'D2 depth bán',imbalance:'D3 imbalance'})[k],(states[v.state]||v.state)+(v.reason?' · '+(reasons[v.reason]||v.reason):''));
  }
  function paint(){if(!data)return;if(isCapture())return paintCapture();const g=M.geometry(data,index,Number($('window').value)||900,side),bubbles=$('showBubbles').checked?M.bubblesAt(tradePack,g.start,g.stop,{seconds:Number($('bubbleGroup').value)||1,minQty:Number($('bubbleMin').value)||0}):[];
    const neutral=tradePack?.unit_status!=='EMPIRICALLY_VERIFIED_FOR_ACB_TRADES';chartLayout=MonitorHeatmap.draw($('heatmap'),g,Number($('scale').value)||250000,bubbles,Number($('bubbleScale').value)||10000,neutral);MonitorHeatmap.timeline($('timeline'),data,index,eventsAt(data,index));
    const status=$('bubbleStatus');status.textContent=tradeError?'Không tải được bubbles: '+tradeError:!tradePack||tradePack.state==='NOT_COLLECTED'?'Chưa có tập giao dịch đã tải. Không suy ra khối lượng khớp bằng 0.':!$('showBubbles').checked?'Đã ẩn volume bubbles.':`${fmt(bubbles.length)} bubbles · ${fmt(bubbles.reduce((n,b)=>n+b.qty,0))} CP trong cửa sổ sau lọc · chỉ đến con trỏ. `+(bubbles.length?'':'Phát lại hoặc tua đến cuối phiên để xem. ')+(neutral?'Màu xám: đơn vị chưa xác minh đầy đủ; hệ số schema giá ×1.000, lượng ×10. ':'')+(tradePack.quality.volume_reconciliation==='RECONCILED_TO_LAST_REPORTED_CUMULATIVE'?'Đối soát cả bộ: khớp bộ đếm lũy kế.':'Đối soát cả bộ chưa khớp; dữ liệu có thể thiếu hoặc không cộng được.');
    $('bubbleFirst').disabled=!tradePack?.trades.length;
  }
  function render(){breakoutUI?.sync();if(!data)return;const f=data.frames[index],events=eventsAt(data,index),shown=filtered(events);$('clock').textContent=clock(f.time);$('sourceClock').textContent=data.symbol+' · '+clock(f.time).slice(0,8);$('position').textContent=`${fmt(index+1)} / ${fmt(data.frames.length)} quan sát`;$('watchQualityClock').textContent=clock(f.time).slice(0,8);$('watchTime').value=clock(f.time).slice(0,8);
    $('spread').textContent=fmt(f.features.spread_bps,2);$('bidDepth').textContent=fmt(f.features.bid_depth);$('askDepth').textContent=fmt(f.features.ask_depth);$('eventCount').textContent=observationOnly()?'—':fmt(events.length);$('detectorState').textContent=observationOnly()?'Chỉ quan sát · chưa đánh giá':(relative()?'D1/D2 tương đối · ':'')+(states[f.features.detector_state]||f.features.detector_state);$('bookQuality').textContent=f.features.eligible?'SPREAD ĐỦ ĐẦU VÀO':reasons[f.features.reason]||'CHƯA ĐỦ ĐIỀU KIỆN';
    $('book').replaceChildren(table(['Mức','Lượng mua','Giá mua','Giá bán','Lượng bán'],Array.from({length:isCapture()?Math.max(f.bids.length,f.asks.length,3):3},(_,i)=>[i+1,fmt(f.bids[i]?.[1]),fmt(f.bids[i]?.[0]),fmt(f.asks[i]?.[0]),fmt(f.asks[i]?.[1])])));
    $('bookUnits').textContent=isCapture()
      ? (data.price_unit==='VND'?'Giá theo đồng':'Giá theo đơn vị nguồn')+' · '+(data.volume_unit==='shares'?'Lượng theo cổ phiếu':'Lượng theo đơn vị nguồn')
      : 'Giá theo đồng · Lượng theo cổ phiếu'+(['EMPIRICALLY_VERIFIED','OFFICIALLY_VERIFIED'].includes(f.units)?'':' · Quy đổi để quan sát');
    $('filterCount').textContent=`${shown.length} / ${events.length} sự kiện đã biết · không lộ các sự kiện tương lai`;eventButtons($('events'),shown);eventButtons($('watchEvents'),events.slice(0,8));renderEvidence(events);quality($('quality'));quality($('watchQuality'));paint();
    if(isCapture()){$('exportCSV').hidden=true;$('exportJSON').hidden=false;$('exportJSON').href=captureExport();}else{$('exportCSV').hidden=false;$('exportJSON').hidden=false;$('exportCSV').href='/api/export?dataset_id='+data.dataset_id+'&format=csv';$('exportJSON').href='/api/export?dataset_id='+data.dataset_id;}
    if(isCapture()){if(captureEventCutoff)$('filterCount').textContent+=' \u00b7 Ki\u1ec3m tra \u0111\u1ebfn '+clock(captureEventCutoff).slice(0,8);captureState();}if(page==='watch')updateWatch();
  }
  async function updateWatch(){if(!data)return;if(isCapture())return renderCaptureWatch();const requested=marketRows().map(e=>e.symbol);if(!requested.length){$('watchTable').replaceChildren();$('watchStatus').textContent='Không có mã khớp bộ lọc.';return;}const token=++watchGeneration,id=data.dataset_id,point=data.frames[index].time;$('watchStatus').textContent='Đang truy vấn cùng mốc nguồn…';try{const rows=await api('/api/watchlist?day='+data.day+'&cutoff='+encodeURIComponent(point)+'&symbols='+encodeURIComponent(requested.join(',')));if(token!==watchGeneration||id!==data?.dataset_id)return;
      $('watchTable').replaceChildren(table(['Mã','Bid1','Ask1','Depth mua','Depth bán','Giờ nguồn','Trạng thái',''],rows.map(r=>[r.symbol,fmt(r.bid),fmt(r.ask),fmt(r.features?.bid_depth),fmt(r.features?.ask_depth),r.time?clock(r.time).slice(0,8):'—',reasons[r.status]||r.status,r.dataset_id?button('Mở',()=>load(r.dataset_id,{page:'analysis',index:null,targetTime:point})): 'Chưa xuất'])));$('watchStatus').textContent='Mốc nguồn '+clock(point);
    }catch(error){if(token===watchGeneration)$('watchStatus').textContent=error.message;}}
  async function loadLibrary(){if(isCapture()){await captureEventQuery();renderCaptureLibrary();return;}const token=generation;$('libraryStatus').textContent='Đang đọc hồ sơ và nhận xét…';try{const rows=await api('/api/library');if(token!==generation)return;library=rows;renderLibrary();if(page==='review')$('error').hidden=true;}catch(e){if(token===generation)$('libraryStatus').textContent=e.message;}}
  function renderLibrary(){if(isCapture())return renderCaptureLibrary();const rows=library.filter(r=>($('includeArchived').checked||!r.archived)&&[[$('librarySymbol').value,r.symbol],[$('libraryDay').value,r.day],[$('libraryType').value,r.event.type],[$('libraryVerdict').value,r.review?.verdict||'unreviewed']].every(([choice,value])=>!choice||choice==='all'||choice===value)&&(!$('librarySearch').value||[r.symbol,r.event.id,r.review?.note||''].join(' ').toLowerCase().includes($('librarySearch').value.toLowerCase())));
    const useful=rows.filter(r=>r.review?.verdict==='useful').length;$('libraryStatus').textContent=`${rows.length} hồ sơ khớp · ${useful} được ghi nhận hữu ích · Thư viện cả phiên, không phải xác suất/độ chính xác detector`;
    $('libraryTable').replaceChildren(table(['Ngày / mã','Sự kiện','Phát hiện','Trạng thái cuối','Rà soát','Ghi chú gần nhất',''],rows.map(r=>[dayLabel(r.day)+' · '+r.symbol+(r.archived?' · bản cũ':''),title(r.event),clock(r.event.detected_at).slice(0,8),states[r.event.state]||r.event.state,verdicts[r.review?.verdict||'unreviewed'],(r.review?.note||'—').slice(0,160),button('Xem bằng chứng',()=>load(r.dataset_id,{page:'analysis',index:r.event.detected_index,eventId:r.event.id}))])));
  }
  function tick(now){if(playing&&data){const target=origin+(now-wall)*Number($('speed').value),next=M.indexAt(data,target);if(now-lastPaint>=100&&next!==index){setIndex(next);lastPaint=now;}if(target>=M.time(data.frames.at(-1).time)){setIndex(data.frames.length-1);stop();}}requestAnimationFrame(tick);}
  function seekTime(value){if(!data||!/^\d\d:\d\d(?::\d\d)?$/.test(value))return;stop();setIndex(M.indexAt(data,M.time(data.day+'T'+(value.length===5?value+':00':value)+'+07:00')));}
  $('dataMode').addEventListener('change',()=>switchMode($('dataMode').value));
  $('captureRun').addEventListener('change',()=>{following=false;loadCapture().catch(e=>showError(e.message));});
  $('pauseCapture').addEventListener('click',()=>{following=false;captureEpoch++;captureEvents++;clearTimeout(eventTimer);eventTimer=null;stop();captureState();if(data)render();});
  $('followLatest').addEventListener('click',async()=>{try{const epoch=pollEpoch;captureStatus=await api('/api/live/status');captureRuns=await api('/api/live/runs');if(!isCapture()||epoch!==pollEpoch)return;const run=captureRuns.find(r=>r.run_id===captureStatus.run_id)||captureRuns[0];following=true;captureOptions($('symbolSelect').value,run?.day,run?.run_id);await loadCapture();}catch(e){showError(e.message);}});
  for(const [id,direction] of [['captureEarlier',-1],['captureLater',1]])$(id).addEventListener('click',()=>{if(!isCapture()||!data)return;following=false;const start=direction<0?Math.max(0,data.frames[0].index-5000):Math.min(capture.window.total-1,data.frames.at(-1).index+1);loadCapture(start).catch(e=>showError(e.message));});
  $('play').addEventListener('click',togglePlayback);
  document.addEventListener?.('keydown',event=>{
    if(event.code!=='Space'||event.repeat||event.defaultPrevented||event.ctrlKey||event.altKey||event.metaKey||
      page!=='analysis'||!data||$('play').disabled||document.querySelector?.('dialog[open], .tool-popover[open]')||
      event.target?.closest?.('button, input, select, textarea, summary, a, [contenteditable]:not([contenteditable="false"])'))return;
    event.preventDefault();
    togglePlayback();
  });
  const compactMenu=window.matchMedia?.('(max-width: 900px)');
  let menuPreference=null;
  try{
    const saved=window.localStorage?.getItem('liquiditylab.menu');
    if(saved==='compact'||saved==='expanded')menuPreference=saved==='compact';
  }catch(_){}
  let menuCollapsed=false;
  function resizeMenu(collapsed){
    menuCollapsed=collapsed;
    $('mainSidebar').dataset.collapsed=String(collapsed);
    const toggle=$('menuToggle'),label=collapsed?'Mở rộng menu':'Thu gọn menu';
    toggle.setAttribute('aria-expanded',String(!collapsed));
    toggle.setAttribute('aria-label',label);
    toggle.title=label;
  }
  resizeMenu(menuPreference??Boolean(compactMenu?.matches));
  $('menuToggle').addEventListener('click',()=>{
    resizeMenu(!menuCollapsed);
    menuPreference=menuCollapsed;
    try{window.localStorage?.setItem('liquiditylab.menu',menuCollapsed?'compact':'expanded');}catch(_){}
  });
  compactMenu?.addEventListener?.('change',event=>{if(menuPreference===null)resizeMenu(event.matches);});
  const popovers=['marketFilters','chartSettings','replayOptions','captureTools'];
  for(const id of popovers){
    const disclosure=$(id);
    disclosure.addEventListener('keydown',event=>{
      if(event.key!=='Escape'||!disclosure.open)return;
      disclosure.open=false;
      disclosure.querySelector('summary')?.focus();
    });
    disclosure.addEventListener('toggle',()=>{
      if(!disclosure.open)return;
      for(const other of popovers)if(other!==id)$(other).open=false;
    });
  }
  document.addEventListener?.('pointerdown',event=>{
    for(const id of popovers){
      const disclosure=$(id);
      if(disclosure.open&&!disclosure.contains(event.target))disclosure.open=false;
    }
  });
  $('cursor').addEventListener('input',()=>{if(data){stop();setIndex($('cursor').value);}});
  for(const [id,d] of [['prev',-1],['next',1]])$(id).addEventListener('click',()=>{if(data){stop();setIndex(index+d);}});
  $('first').addEventListener('click',()=>{if(data){stop();setIndex(0);}});
  for(const id of ['sessionEnd','watchEnd'])$(id).addEventListener('click',()=>{if(data){stop();setIndex(data.frames.length-1);}});
  $('seek').addEventListener('click',()=>seekTime($('seekTime').value));$('applyWatchTime').addEventListener('click',()=>seekTime($('watchTime').value));
  for(const [id,forward] of [['nextEvent',true],['previousEvent',false]])$(id).addEventListener('click',()=>{if(!data)return;const candidates=allEvents.filter(e=>forward?e.detected_index>index:e.detected_index<index).sort((a,b)=>forward?a.detected_index-b.detected_index:b.detected_index-a.detected_index);if(candidates.length){if(isCapture())return jumpCaptureEvent(candidates[0]);stop();selected=candidates[0].id;setIndex(candidates[0].detected_index);}});
  $('speed').addEventListener('change',()=>{if(data){origin=M.time(data.frames[index].time);wall=performance.now();}});
  for(const id of ['window','scale'])$(id).addEventListener('change',paint);
  for(const id of ['showBubbles','bubbleGroup','bubbleMin','bubbleScale'])$(id).addEventListener('change',paint);
  $('bubbleFirst').addEventListener('click',()=>{if(!data||!tradePack?.trades.length)return;stop();const t=M.time(tradePack.trades[0].time);let i=M.indexAt(data,t);if(M.time(data.frames[i].time)<t&&i<data.frames.length-1)i++;setIndex(i);});
  for(const id of ['eventType','eventState','eventVerdict'])$(id).addEventListener('change',render);
  for(const b of document.querySelectorAll('[data-side]'))b.addEventListener('click',()=>{side=b.dataset.side;for(const x of document.querySelectorAll('[data-side]'))x.classList.toggle('selected',x===b);paint();});
  for(const b of document.querySelectorAll('[data-page]'))b.addEventListener('click',()=>setPage(b.dataset.page));
  $('watchToAnalysis').addEventListener('click',()=>setPage('analysis'));
  $('daySelect').addEventListener('change',choose);$('symbolSelect').addEventListener('change',choose);$('dataset').addEventListener('change',()=>load($('dataset').value));
  $('applySymbol').addEventListener('click',choose);
  for(const id of ['marketSearch','marketExchange'])$(id).addEventListener(id==='marketSearch'?'input':'change',()=>{marketPage=0;symbolOptions(data?.symbol);renderInventory();if(page==='watch')updateWatch();});
  for(const [id,step] of [['marketPrev',-1],['marketNext',1]])$(id).addEventListener('click',()=>{marketPage+=step;renderInventory();if(page==='watch')updateWatch();});
  for(const id of ['librarySymbol','libraryDay','libraryType','libraryVerdict','includeArchived'])$(id).addEventListener('change',renderLibrary);$('librarySearch').addEventListener('input',renderLibrary);$('refreshLibrary').addEventListener('click',loadLibrary);
  $('showMethod').addEventListener('click',()=>$('method').showModal());$('closeMethod').addEventListener('click',()=>$('method').close());$('openConfig').addEventListener('click',()=>{if(!readOnly&&!isCapture())$('configDialog').showModal();});$('closeConfig').addEventListener('click',()=>$('configDialog').close());
  $('reviewForm').addEventListener('submit',async event=>{event.preventDefault();if(readOnly||!data||!selected)return;if(isCapture())return saveCaptureReview();const datasetId=data.dataset_id,eid=selected;$('saveReview').disabled=true;try{await api('/api/reviews',{method:'POST',headers:{'Content-Type':'application/json','X-Monitor-CSRF':csrf},body:JSON.stringify({dataset_id:datasetId,event_id:eid,verdict:$('verdict').value,note:$('note').value})});const rows=await api('/api/reviews?dataset_id='+datasetId);if(data?.dataset_id===datasetId&&selected===eid){notes=rows;$('reviewForm').dataset.event='';renderEvidence(eventsAt(data,index));$('reviewState').textContent=(hosted?'Đã lưu trên máy chủ · giữ toàn bộ lịch sử nhận xét.':'Đã lưu trên máy · giữ toàn bộ lịch sử nhận xét.');}}catch(e){$('reviewState').textContent=e.message;}finally{$('saveReview').disabled=false;}});
  $('configForm').addEventListener('submit',async e=>{e.preventDefault();if(readOnly||isCapture()||!data)return;stop();const day=data.day,symbol=data.symbol;$('reanalyze').disabled=true;$('configState').textContent='Đang xử lý dữ liệu đã lưu, giữ nguyên phiên bản trước…';try{const entry=await api('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json','X-Monitor-CSRF':csrf},body:JSON.stringify({day,symbol,parameters:{spread_multiplier:$('cfgMultiplier').value,min_increase_bps:$('cfgIncrease').value,depth_drop:String(Number($('cfgDepth').value)/100),imbalance_threshold:String(Number($('cfgImbalance').value)/100),duration_seconds:Number($('cfgDuration').value)}})});await refreshCatalog();await load(entry.dataset_id);$('configState').textContent='Đã tạo phiên bản phân tích. Nhận xét của phiên bản cũ vẫn còn trong thư viện.';}catch(error){$('configState').textContent=error.message;}finally{$('reanalyze').disabled=false;}});
  $('timeline').addEventListener('click',e=>{if(!data)return;const rect=$('timeline').getBoundingClientRect(),fraction=Math.max(0,Math.min(1,(e.clientX-rect.left-12)/(rect.width-24))),a=M.time(data.frames[0].time),b=M.time(data.frames.at(-1).time);stop();setIndex(M.indexAt(data,a+fraction*(b-a)));});
  $('heatmap').addEventListener('mousemove',e=>{if(!data)return;const rect=$('heatmap').getBoundingClientRect();$('hover').hidden=false;
    const hit=chartLayout?.hits.filter(h=>Math.hypot(e.clientX-rect.left-h.x,e.clientY-rect.top-h.y)<=h.radius+3).sort((a,b)=>a.radius-b.radius)[0];
    if(hit){const b=hit.bubble;$('hover').textContent=`KHỚP LỆNH · ${b.side==='BUY'?'Mua chủ động':b.side==='SELL'?'Bán chủ động':'Chưa rõ phía'} (phân loại của nguồn)\nGiá ${fmt(b.price)} ${isCapture()&&data.price_unit!=='VND'?'theo nguồn':'VND'} · ${fmt(b.qty)} ${isCapture()&&data.volume_unit!=='shares'?'đơn vị nguồn':'CP'}\n${fmt(b.count)} giao dịch · ${clock(new Date(b.first).toISOString())} – ${clock(new Date(b.last).toISOString())}`;return;}
    const g=M.windowAt(data,index,Number($('window').value)),fraction=Math.max(0,Math.min(1,(e.clientX-rect.left-65)/(rect.width-77))),target=g.start+fraction*(g.stop-g.start),f=data.frames[Math.min(index,M.indexAt(data,target))],age=(target-M.time(f.time))/1000;$('hover').textContent=age<0||age>data.config.max_observation_gap_seconds?'Chưa có quan sát gần. Không suy ra lượng bằng 0.':`${clock(f.time)}\nBid1 ${fmt(f.bids[0][0])} · ${fmt(f.bids[0][1])}\nAsk1 ${fmt(f.asks[0][0])} · ${fmt(f.asks[0][1])}\nSpread ${fmt(f.features.spread_bps,2)} bps`;});$('heatmap').addEventListener('mouseleave',()=>{$('hover').hidden=true;});
  new ResizeObserver(paint).observe($('heatmap').parentElement);requestAnimationFrame(tick);
  let bootRetries=0;
  async function boot(){try{
    if(isCapture())return;
    modeControls();if(typeof location!=='undefined'&&(location.pathname.startsWith('/realtime')||new URLSearchParams(location.search).get('view')==='capture')){await switchMode('capture');return;}
    const epoch=pollEpoch;
    const response=await fetch('/api/hosting',{cache:'no-store'});
    if(response.ok){const info=await response.json();hosted=Boolean(info.hosted);if(hosted&&!info.seed_ready){$('connection').textContent='Đang khôi phục dữ liệu đã lưu…';$('provenance').textContent='Máy chủ đang chuẩn bị dữ liệu. Trang sẽ tự cập nhật.';setTimeout(boot,3000);return;}}
    await refreshCatalog();if(isCapture()||epoch!==pollEpoch)return;const entry=currentEntries().find(e=>e.symbol==='ACB')||currentEntries()[0];await load(entry.dataset_id);
  }catch(e){showError(e.message);$('connection').textContent='Chưa sẵn sàng';if(!data&&bootRetries++<12)setTimeout(boot,5000);}}
  boot();
})();
