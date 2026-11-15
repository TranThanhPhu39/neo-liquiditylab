'use strict';
const SSILiveModel=(()=>{
  const time=EventReplay.time,num=EventReplay.numeric;
  class Window {
    constructor(){this.frames=[];this.trades=[];this.events=[];this.run=null;this.symbol=null;this.total=0;}
    accept(p){
      if(!p||!Array.isArray(p.frames)||!Array.isArray(p.trades)||p.frames.length>5000)throw Error('Dữ liệu SSI không hợp lệ.');
      const reset=p.reset||p.run_id!==this.run||p.symbol!==this.symbol;
      let previous=reset?null:this.frames.at(-1);
      for(const f of p.frames){
        time(f.time);time(f.received_at);
        if(!Number.isSafeInteger(f.index)||f.index<0||!f.features||!['bids','asks'].every(s=>Array.isArray(f[s])&&f[s].length<=10&&f[s].every(x=>Array.isArray(x)&&x.length===2&&(x[0]===null||num(x[0])>0)&&(x[1]===null||Number.isSafeInteger(x[1])&&x[1]>=0))))throw Error('Sổ lệnh SSI không hợp lệ.');
        if(previous&&(f.index<=previous.index||time(f.time)<time(previous.time)))throw Error('Đồng hồ nhận hoặc thứ tự bản tin bị gián đoạn.');
        previous=f;
      }
      for(const t of p.trades){if(!/^[a-f0-9]{64}$/.test(t.id)||num(t.price)<=0||!Number.isSafeInteger(t.qty)||t.qty<=0||!['BUY','SELL','UNSPECIFIED'].includes(t.side))throw Error('Giao dịch SSI không hợp lệ.');time(t.time);}
      if(reset){this.frames=[];this.trades=[];}
      this.frames.push(...p.frames);if(this.frames.length>15000)this.frames.splice(0,this.frames.length-15000);
      const ids=new Set(this.trades.map(t=>t.id));for(const t of p.trades)if(!ids.has(t.id)){this.trades.push(t);ids.add(t.id);}
      const first=this.frames[0];if(first)this.trades=this.trades.filter(t=>time(t.time)>=time(first.time));
      this.trades.sort((a,b)=>time(a.time)-time(b.time));
      this.run=p.run_id;this.symbol=p.symbol;this.total=p.total;this.events=p.events||[];this.units=p.unit_status;this.priceUnit=p.price_unit;this.volumeUnit=p.volume_unit;
    }
    geometry(index,seconds,side='both',liveStop=null){
      const selected=this.frames[index];if(!selected)return null;
      const stop=liveStop??time(selected.time),start=stop-seconds*1000;
      const frames=this.frames.slice(0,index+1).filter(f=>time(f.time)>=start);
      const cells=[],bid=[],ask=[],gaps=[];let previous=null;
      for(const f of frames){const t=time(f.time);
        if(previous&&(t-time(previous.time)>30000||previous.connection!==f.connection))gaps.push([time(previous.time),t]);
        previous=f;
        for(const s of ['bids','asks'])if(side==='both'||s===side)for(const [p,q] of f[s])if(num(p)!==null&&num(q)!==null)cells.push({t,price:Number(p),qty:Number(q),side:s});
        bid.push([t,num(f.bids[0]?.[0])]);ask.push([t,num(f.asks[0]?.[0])]);
      }
      if(previous&&stop-time(previous.time)>30000)gaps.push([time(previous.time),stop]);
      const cutoff=time(selected.time);
      return {start,stop,frames,cells,bid,ask,gaps,maxGapMs:30000,
        events:this.events.filter(e=>time(e.detected_received_at||e.observed_at)<=cutoff).map(e=>({...e,detected_at:e.detected_received_at||e.observed_at}))};
    }
    bubbles(g,seconds=1){
      if(!g||!g.cells.length)return {rows:[],scaleMismatch:false};
      const mid=g.cells[Math.floor(g.cells.length/2)].price;
      const trades=this.trades.filter(t=>Number(t.price)/mid>=.05&&Number(t.price)/mid<=20);
      return {rows:EventReplay.bubblesAt({trades},g.start,g.stop,{seconds}),scaleMismatch:trades.length!==this.trades.length};
    }
  }
  return {Window,time};
})();
if(typeof module!=='undefined')module.exports=SSILiveModel;
