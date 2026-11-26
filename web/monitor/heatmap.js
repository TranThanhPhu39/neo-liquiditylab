'use strict';
const MonitorHeatmap = (() => {
  const number = x => new Intl.NumberFormat('vi-VN', {maximumFractionDigits: 0}).format(x);
  const clock = t => new Date(t + 7 * 3600000).toISOString().slice(11, 19);
  function draw(canvas, geometry, scale = 250000, bubbles=[],bubbleScale=10000,neutral=false) {
    const rect = canvas.getBoundingClientRect(), w = Math.max(300, rect.width), h = Math.max(200, rect.height);
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = w * ratio; canvas.height = h * ratio;
    const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio);
    ctx.fillStyle = '#17211f'; ctx.fillRect(0, 0, w, h);
    const left = 65, right = 12, top = 18, bottom = 35, iw = w - left - right, ih = h - top - bottom;
    const prices = [...geometry.cells.map(c => c.price),...bubbles.map(b=>b.price)], all = [...new Set(prices)].sort((a, b) => a - b);
    let step = Infinity; for (let i = 1; i < all.length; i++) step = Math.min(step, all[i] - all[i - 1]);
    if (!Number.isFinite(step) || step <= 0) step = 50;
    const low = prices.length ? prices.reduce((a,b)=>Math.min(a,b),Infinity) - step : 0, high = prices.length ? prices.reduce((a,b)=>Math.max(a,b),-Infinity) + step : 1;
    const x = t => left + (t - geometry.start) / Math.max(1, geometry.stop - geometry.start) * iw;
    const y = p => top + (high - p) / Math.max(1, high - low) * ih;
    ctx.save(); ctx.beginPath(); ctx.rect(left, top, iw, ih); ctx.clip();
    for (const [a, b] of geometry.gaps) {
      const x1 = x(a), x2 = x(b); ctx.fillStyle = '#88988d0c'; ctx.fillRect(x1, top, x2 - x1, ih);
      ctx.strokeStyle = '#5a6a5f40'; ctx.lineWidth = 1;
      for (let px = x1 - ih; px < x2; px += 12) { ctx.beginPath(); ctx.moveTo(Math.max(x1, px), top + Math.max(0, x1 - px)); ctx.lineTo(Math.min(x2, px + ih), top + Math.min(ih, x2 - px)); ctx.stroke(); }
    }
    for (const c of geometry.cells) {
      const yy = y(c.price), rh = Math.max(3, Math.abs(y(c.price + step / 2) - y(c.price - step / 2)) * .85);
      const alpha = .12 + .83 * Math.min(1, Math.sqrt(c.qty / scale));
      ctx.fillStyle = c.side === 'bids' ? `rgba(34,184,160,${alpha})` : `rgba(230,161,88,${alpha})`;
      // Each observation gets one vertical mark. No unproven hold or interpolation.
      if (c.qty === 0) { ctx.strokeStyle = '#819087'; ctx.strokeRect(x(c.t), yy - rh / 2, 1, rh); }
      else ctx.fillRect(x(c.t) - .5, yy - rh / 2, 1.2, rh);
    }
    for (const [points, color] of [[geometry.bid, '#8ad8bd'], [geometry.ask, '#f2c28b']]) {
      ctx.strokeStyle = color; ctx.lineWidth = 1.25; ctx.beginPath(); let previous = null;
      for (const [t, p] of points) {
        if(p===null){previous=null;continue;}
        if (previous === null || t - previous > geometry.maxGapMs) ctx.moveTo(x(t), y(p)); else ctx.lineTo(x(t), y(p));
        previous = t;
      }
      ctx.stroke();
    }
    const hits=[];
    // Area scales with volume up to the disclosed radius cap; exact volume remains in hover.
    for(const b of [...bubbles].sort((a,b)=>b.qty-a.qty)){
      const radius=Math.max(2,Math.min(26,10*Math.sqrt(b.qty/bubbleScale))),xx=x(b.t),yy=y(b.price);
      ctx.beginPath();ctx.arc(xx,yy,radius,0,Math.PI*2);
      ctx.fillStyle=neutral||b.side==='UNSPECIFIED'?'rgba(190,200,207,.66)':b.side==='BUY'?'rgba(67,220,167,.72)':'rgba(255,113,116,.72)';
      ctx.fill();ctx.strokeStyle='#e9f4ebaa';ctx.lineWidth=.75;ctx.stroke();hits.push({x:xx,y:yy,radius,bubble:b});
    }
    for (const e of geometry.events) {
      const t = EventReplay.time(e.detected_at); if (t < geometry.start) continue;
      ctx.strokeStyle = '#b4c0f0'; ctx.setLineDash([3, 4]); ctx.beginPath(); ctx.moveTo(x(t), top); ctx.lineTo(x(t), top + ih); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = '#b4c0f0'; ctx.beginPath(); ctx.moveTo(x(t), top + 2); ctx.lineTo(x(t) - 4, top - 6); ctx.lineTo(x(t) + 4, top - 6); ctx.fill();
    }
    ctx.restore(); ctx.font = '10px Segoe UI, Arial'; ctx.fillStyle = '#9bad9e';
    ctx.textAlign = 'right';
    for (let i = 0; i <= 5; i++) { const p = low + (high - low) * i / 5, yy = y(p); ctx.fillText(number(p), left - 10, yy + 3); ctx.strokeStyle = '#a4bca214'; ctx.beginPath(); ctx.moveTo(left, yy); ctx.lineTo(w - right, yy); ctx.stroke(); }
    ctx.textAlign = 'center';
    for (let i = 0; i <= 4; i++) { const t = geometry.start + (geometry.stop - geometry.start) * i / 4; ctx.fillText(clock(t), x(t), h - 12); }
    if (!prices.length) { ctx.fillStyle = '#bbc6bd'; ctx.fillText('Chưa có mức giá hợp lệ tại thời điểm này.', w / 2, h / 2); }
    return {x, y, left, iw, w, h,hits};
  }
  function timeline(canvas,data,index,events){
    const bounds=canvas.getBoundingClientRect(), w=Math.max(240,bounds.width), h=Math.max(26,bounds.height||70);
    const ratio=Math.min(2,window.devicePixelRatio||1), rail=Math.min(20,h*.21), height=Math.min(12,h*.18);
    canvas.width=w*ratio;canvas.height=h*ratio;const c=canvas.getContext('2d');c.scale(ratio,ratio);c.fillStyle='#f5f5f7';c.fillRect(0,0,w,h);
    const start=EventReplay.time(data.frames[0].time),stop=EventReplay.time(data.frames.at(-1).time),now=EventReplay.time(data.frames[index].time),x=t=>12+(t-start)/Math.max(1,stop-start)*(w-24);
    c.fillStyle='#dcdde4';c.fillRect(12,rail,w-24,height);c.fillStyle='#9290bc';c.fillRect(12,rail,Math.max(0,x(now)-12),height);
    for(const e of events){c.fillStyle=({SPREAD_WIDENING:'#516d96',DEPTH_DROP:'#b9763a',IMBALANCE_PERSISTENCE:'#398779'})[e.type]||'#657367';c.fillRect(x(EventReplay.time(e.detected_at))-1,rail-2,2,height+4);}
    c.fillStyle='#302b66';c.fillRect(x(now)-1,rail-3,2,height+6);c.font='9px Segoe UI,Arial';c.fillStyle='#575766';c.textAlign='left';c.fillText(clock(start),12,h-4);c.textAlign='right';c.fillText(clock(stop),w-12,h-4);
  }
  return {draw,timeline};
})();
