'use strict';
/* Lazy, bounded Python worker. A failed runtime cannot block descriptive charts. */
class EngineClient {
  constructor({onstate=()=>{},createWorker=url=>new Worker(url),startupMs=90000,requestMs=30000}={}){
    this.onstate=onstate;this.createWorker=createWorker;this.startupMs=startupMs;this.requestMs=requestMs;
    this.pending=new Map();this.nextId=0;this.error=null;this.ready=null;
  }
  fail(error){
    if(this.error)return;this.error=error;clearTimeout(this.startupTimer);this.rejectReady?.(error);
    for(const item of this.pending.values()){clearTimeout(item.timer);item.reject(error);}this.pending.clear();
    this.worker?.terminate();this.onstate('failed');
  }
  start(){
    if(this.ready)return this.ready;
    this.onstate('loading');
    this.ready=new Promise((resolve,reject)=>{this.resolveReady=resolve;this.rejectReady=reject;});
    try{
      this.worker=this.createWorker('./engine-worker.js?v=20261105-svg1');
      this.startupTimer=setTimeout(()=>this.fail(new Error('Bộ tính Python tải quá lâu. Hãy tải lại trang để thử A1/B5; biểu đồ thanh khoản vẫn sử dụng được.')),this.startupMs);
      this.worker.onmessage=({data})=>{
        if(this.error)return;
        if(data.ready){clearTimeout(this.startupTimer);this.resolveReady();this.onstate('ready');return;}
        if(data.fatal){this.fail(new Error('Không khởi động được bộ tính Python. Hãy tải lại trang để thử A1/B5.'));return;}
        const item=this.pending.get(data.id);if(!item)return;this.pending.delete(data.id);clearTimeout(item.timer);
        data.error?item.reject(new Error(data.error)):item.resolve(data.result);
      };
      this.worker.onerror=()=>this.fail(new Error('Lỗi tải bộ tính Python. Hãy tải lại trang để thử A1/B5.'));
      this.worker.onmessageerror=()=>this.fail(new Error('Không đọc được kết quả bộ tính Python.'));
    }catch(error){this.fail(new Error('Trình duyệt chưa khởi động được bộ tính Python.'));}
    return this.ready;
  }
  async call(method,text){
    await this.start();if(this.error)throw this.error;
    return new Promise((resolve,reject)=>{
      const id=++this.nextId,timer=setTimeout(()=>this.fail(new Error('Bộ tính Python không trả kết quả kịp thời. Hãy tải lại trang để thử A1/B5.')),this.requestMs);
      this.pending.set(id,{resolve,reject,timer});
      try{this.worker.postMessage({id,method,text});}catch(error){this.fail(new Error('Không gửi được dữ liệu tới bộ tính Python.'));}
    });
  }
}
if(typeof module!=='undefined')module.exports=EngineClient;
