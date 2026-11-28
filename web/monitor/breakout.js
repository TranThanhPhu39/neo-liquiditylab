'use strict';
const MonitorBreakout = (() => {
  const states={READY:'Đã tính trên dữ liệu quan sát',NO_OBSERVED_TRADES:'Chưa có giao dịch khớp riêng; không suy ra giá khớp từ sổ lệnh.',REFERENCE_REQUIRED:'Chưa có mức tham chiếu hợp lệ. Nhập đỉnh đã kiểm tra hoặc chọn ngày có giao dịch.',TRADE_UNITS_UNAVAILABLE:'Chưa có đơn vị giá và lượng giao dịch phù hợp.',TRADE_UNITS_UNVERIFIED:'Đơn vị giao dịch chưa đủ điều kiện nghiên cứu.',RESOURCE_LIMIT_PARTIAL:'Đạt giới hạn 100 lần vượt; đây là kết quả một phần.'};
  const outcomeStates={PENDING:'Chưa tới mốc',SESSION_BOUNDARY:'Qua nghỉ trưa / hết phiên liên tục',MISSING_ENDPOINT:'Thiếu giá khớp gần mốc',PARTIAL_WINDOW:'Quan sát thưa / chưa đối soát đủ',OBSERVED_ENDPOINT:'Có giá quan sát gần mốc'};
  const groups={ALL:'Tất cả lần vượt',POSITIVE_IMBALANCE_TIGHT_SPREAD:'Imbalance > 0 · spread ≤ median quá khứ',OTHER_OBSERVED_BOOK:'Sổ lệnh đủ mẫu · điều kiện khác',UNAVAILABLE:'Thiếu sổ lệnh / baseline đủ điều kiện'};
  const format=(v,n=0)=>v===null||v===undefined||!Number.isFinite(Number(v))?'—':new Intl.NumberFormat('vi-VN',{maximumFractionDigits:n}).format(Number(v));
  const clock=t=>new Date(Date.parse(t)+7*3600000).toISOString().slice(11,19);
  function outcomeLabel(o){
    if(!o)return 'Chưa có';
    const status=outcomeStates[o.state]||o.state;
    if(o.price===undefined)return status;
    return `${format(o.price)} · ${o.above_reference?'trên':'không trên'} mức · ${format(o.return_bps/100,2)}%${o.state==='PARTIAL_WINDOW'?' · mẫu một phần':''}`;
  }
  function signature(c){return c?.data&&!c.capture?c.data.dataset_id+'|'+c.cutoff:'';}
  function create({document,api,context,onEnd,onSeek}){
    const $=id=>document.getElementById(id),el=(tag,value='',cls='')=>{const e=document.createElement(tag);e.textContent=value;if(cls)e.className=cls;return e;};
    let result=null,request=0,lastSignature='',loadedId=null,loading=false;
    function table(headers,rows){const t=el('table'),head=el('thead'),tr=el('tr'),body=el('tbody');for(const v of headers)tr.append(el('th',v));head.append(tr);t.append(head);for(const row of rows){const r=el('tr');for(const value of row){const td=el('td');if(typeof value==='object'&&value!==null)td.append(value);else td.textContent=value;r.append(td);}body.append(r);}t.append(body);return t;}
    function invalidate(message){request++;loading=false;result=null;$('breakoutResult').hidden=true;$('breakoutExport').hidden=true;$('breakoutStatus').textContent=message;}
    function sync(){
      const c=context(),key=signature(c);
      $('breakoutRun').disabled=!key||loading;$('breakoutEnd').disabled=!key||loading;
      if(key===lastSignature)return;
      lastSignature=key;
      if(c?.data?.dataset_id!==loadedId){loadedId=c?.data?.dataset_id;$('breakoutDay').value='';$('breakoutPrice').value='';}
      if(c?.data?.day)$('breakoutDay').max=new Date(Date.parse(c.data.day+'T00:00:00Z')-86400000).toISOString().slice(0,10);
      invalidate(c?.capture?'Nghiên cứu hiện dùng lịch sử có giao dịch khớp riêng. Chuyển sang Lịch sử; chưa ghép bản thu với lịch sử khác nguồn.':key?'Mốc nghiên cứu '+clock(c.cutoff)+' · Bấm phân tích để xem. Kết quả cũ đã ẩn khi đổi mốc hoặc mã.':'Đang chờ bộ dữ liệu lịch sử.');
      $('breakoutRun').disabled=!key;$('breakoutEnd').disabled=!key;
    }
    function draw(){
      if(!result||$('breakoutResult').hidden||!$('breakoutPanel').open)return;
      const canvas=$('breakoutChart'),rect=canvas.getBoundingClientRect(),w=Math.max(300,rect.width),h=250,ratio=Math.min(2,globalThis.devicePixelRatio||1);
      canvas.width=w*ratio;canvas.height=h*ratio;const c=canvas.getContext('2d');c.scale(ratio,ratio);c.fillStyle='#0e2848';c.fillRect(0,0,w,h);
      const bars=result.chart||[];if(!bars.length)return;
      const ref=Number(result.reference.price),values=bars.flatMap(b=>[Number(b.low),Number(b.high)]);if(ref>0)values.push(ref);
      let low=Math.min(...values),high=Math.max(...values),pad=Math.max(1,(high-low)*.1);low-=pad;high+=pad;
      const left=65,right=w-15,top=22,bottom=h-32,start=Date.parse(bars[0].time),stop=Math.max(start+60000,Date.parse(bars.at(-1).time));
      const x=t=>left+(t-start)/(stop-start)*(right-left),y=p=>bottom-(p-low)/(high-low)*(bottom-top);
      c.font='11px Segoe UI, Arial';c.fillStyle='#a9bbd7';c.textAlign='right';
      for(let i=0;i<=4;i++){const p=low+(high-low)*i/4;c.fillText(format(p),left-8,y(p)+3);c.strokeStyle='#263652';c.beginPath();c.moveTo(left,y(p));c.lineTo(right,y(p));c.stroke();}
      c.strokeStyle='#eab079';c.setLineDash([6,4]);c.beginPath();c.moveTo(left,y(ref));c.lineTo(right,y(ref));c.stroke();c.setLineDash([]);
      c.fillStyle='#f4cda7';c.textAlign='left';c.fillText('Mức tham chiếu '+format(ref),left+5,14);
      c.strokeStyle='#8de3d0';c.lineWidth=1.6;c.beginPath();let last=null;
      for(const b of bars){const t=Date.parse(b.time),xx=x(t),yy=y(Number(b.close));if(last===null||t-last>90000)c.moveTo(xx,yy);else c.lineTo(xx,yy);last=t;}c.stroke();
      c.strokeStyle='#628b9f';c.lineWidth=1;for(const b of bars){c.beginPath();c.moveTo(x(Date.parse(b.time)),y(Number(b.low)));c.lineTo(x(Date.parse(b.time)),y(Number(b.high)));c.stroke();}
      for(const e of result.episodes){c.fillStyle=e.crossing_status==='OBSERVED_CROSSING'?'#eab079':'#8fa2bb';c.beginPath();c.arc(x(Date.parse(e.time)),y(Number(e.price)),3,0,2*Math.PI);c.fill();}
      c.fillStyle='#a9bbd7';for(let i=0;i<=4;i++){const t=start+(stop-start)*i/4;c.textAlign=i===4?'right':'center';c.fillText(clock(new Date(t).toISOString()),x(t),h-10);}
    }
    function render(r){
      $('breakoutStatus').textContent=(states[r.state]||r.state)+' · '+r.symbol+' · đến '+clock(r.cutoff)+(r.quality?' · '+format(r.quality.trades_to_cutoff)+' giao dịch khớp':'');
      $('breakoutReference').textContent=(r.reference.kind==='USER_SUPPLIED_PREVIOUS_HIGH'?'Mức bạn nhập':'Đỉnh giao dịch đã lưu (thăm dò)')+' · '+r.reference.day+' · '+format(r.reference.price)+' VNĐ. '+r.reference.notice+' Không xác nhận nến ngày đóng cửa trên mức này.';
      $('breakoutResult').hidden=false;$('breakoutExport').hidden=false;
      const count=h=>h.eligible?`${h.above_reference}/${h.eligible} trên mức tại mốc · ${format(h.median_return_bps/100,2)}% median`:'0 mẫu đủ điều kiện';
      $('breakoutGroups').replaceChildren(table(['Nhóm mô tả','Lần vượt giữ lại','Sau 5 phút','Sau 15 phút'],(r.groups||[]).map(g=>[groups[g.group]||g.group,g.episodes,count(g.horizons['5']),count(g.horizons['15'])])));
      const rows=(r.episodes||[]).map(e=>{
        const b=el('button','Xem sổ lệnh','outline');b.type='button';b.addEventListener('click',()=>onSeek(e.time));
        const book=e.book?`${format(e.book.spread_bps,2)} bps · I=${format(e.book.imbalance,2)} · book trước ${format(e.book.age_seconds,1)}s${e.book.baseline_spread_bps===null?' · thiếu baseline':''}`:'Chưa đủ book mới / đơn vị';
        return [clock(e.time)+(e.crossing_status==='SPARSE_OBSERVED_CROSSING'?' · khoảng thưa':''),format(e.price),book,outcomeLabel(e.outcomes['5']),outcomeLabel(e.outcomes['15']),b];
      });
      $('breakoutEpisodes').replaceChildren(rows.length?table(['Lần vượt quan sát','Giá khớp','Sổ lệnh có trước lần vượt','5 phút','15 phút','Phát lại'],rows):el('p',states[r.state]||'Chưa quan sát thấy lần vượt từ dưới/bằng lên trên mức trong phạm vi này. Bắt đầu bản thu khi giá đã trên mức không được tính là lần vượt.','empty-text'));
      draw();
    }
    async function run(end=false){
      if(end)onEnd();sync();const c=context();if(!signature(c))return;
      const key=signature(c),ticket=++request,id=c.data.dataset_id;
      let url='/api/research/breakout?dataset_id='+encodeURIComponent(id)+'&cutoff='+encodeURIComponent(c.cutoff);
      if($('breakoutPrice').value.trim())url+='&reference_price='+encodeURIComponent($('breakoutPrice').value.trim());
      if($('breakoutDay').value)url+='&reference_day='+encodeURIComponent($('breakoutDay').value);
      loading=true;$('breakoutRun').disabled=true;$('breakoutEnd').disabled=true;$('breakoutResult').hidden=true;$('breakoutExport').hidden=true;$('breakoutStatus').textContent='Đang đọc giao dịch khớp và sổ lệnh theo mốc đã chọn…';
      try{const packet=await api(url);if(ticket!==request||signature(context())!==key)return;result=packet;render(packet);}
      catch(e){if(ticket===request)$('breakoutStatus').textContent='Chưa phân tích được: '+e.message;}
      finally{if(ticket===request){loading=false;sync();}}
    }
    $('breakoutRun').addEventListener('click',()=>run());$('breakoutEnd').addEventListener('click',()=>run(true));
    for(const id of ['breakoutDay','breakoutPrice'])$(id).addEventListener('input',()=>{invalidate('Tham chiếu đã đổi. Bấm phân tích để tính lại.');sync();});
    $('breakoutPanel').addEventListener('toggle',draw);
    $('breakoutExport').addEventListener('click',()=>{if(!result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download=`liquiditylab-breakout-${result.symbol}-${result.day}.json`;a.click();URL.revokeObjectURL(url);});
    return {sync,run,draw};
  }
  return {create,outcomeLabel,signature};
})();
if(typeof module!=='undefined')module.exports=MonitorBreakout;
