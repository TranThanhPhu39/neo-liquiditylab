'use strict';
// Adapt captured frames to the shared viewer without changing units or clocks.
const MonitorCapture=(()=>{
  class Replay {
    constructor(){this.window=new SSILiveModel.Window();this.meta=null;}
    accept(packet){
      if(!/^[a-f0-9]{32}$/.test(packet?.run_id)||!/^[A-Z][A-Z0-9]{1,11}$/.test(packet.symbol)||
         !/^[a-f0-9]{64}$/.test(packet.dataset_id)||!/^\d{4}-\d{2}-\d{2}$/.test(packet.day)||
         !Number.isSafeInteger(packet.total)||packet.total<0)throw Error('Bản thu không đúng phạm vi.');
      this.window.accept(packet);this.meta={run_id:packet.run_id,symbol:packet.symbol,day:packet.day,dataset_id:packet.dataset_id,
        provider:'ssi',price_unit:packet.price_unit,volume_unit:packet.volume_unit,clock_policy:packet.clock_policy};
      return this.data();
    }
    data(){return this.meta?{...this.meta,frames:this.window.frames,event_patches:[],config:{max_observation_gap_seconds:30}}:null;}
    setEvents(rows,cutoff){
      if(!Array.isArray(rows)||rows.some(e=>!/^[a-f0-9]{64}$/.test(e.id)||!Number.isInteger(e.detected_index)||
         !e.observed_at||!e.detected_received_at))throw Error('Bằng chứng bản thu không hợp lệ.');
      this.window.events=rows.filter(e=>EventReplay.time(e.observed_at)<=EventReplay.time(cutoff)&&EventReplay.time(e.detected_received_at)<=EventReplay.time(e.observed_at));
    }
    eventsAt(index){
      const f=this.window.frames[index];if(!f)return [];
      return this.window.events.filter(e=>EventReplay.time(e.observed_at)<=EventReplay.time(f.time)).map(e=>{
        const at=this.window.frames.findIndex(f=>f.index===e.detected_index);
        return {...e,detected_at:e.detected_received_at,detected_index:at<0?0:at,
          capture_index:e.detected_index,capture_event:e};
      }).sort((a,b)=>b.capture_index-a.capture_index);
    }
    geometry(index,seconds,side,stop=null){
      const g=this.window.geometry(index,seconds,side,stop);if(g)g.events=this.eventsAt(index);return g;
    }
    label(status,index,following,now=Date.now()){
      const f=this.window.frames[index],current=status.run_id===this.meta?.run_id;
      const age=f?(now-EventReplay.time(f.time))/1000:null;
      const sourceAge=status.quote_ages?.[this.meta?.symbol]??status.quote_age_seconds;
      if(following&&current&&status.active&&!status.stale&&age!==null&&age>=0&&age<=15&&sourceAge!==null&&sourceAge<=15)
        return 'ĐANG CẬP NHẬT';
      if(!following)return 'ĐANG XEM LẠI';
      return !f?'CHỜ DỮ LIỆU':current&&status.active?'DỮ LIỆU TRỄ':'BẢN THU ĐÃ LƯU';
    }
  }
  return {Replay};
})();
if(typeof module!=='undefined')module.exports=MonitorCapture;
