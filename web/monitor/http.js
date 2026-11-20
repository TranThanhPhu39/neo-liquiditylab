'use strict';
const MonitorHTTP=(()=>{
  const messages={
    DATA_RESTORING_RETRY:'Máy chủ chưa phục hồi dữ liệu xong. Trang sẽ thử lại khi bạn mở lại chế độ xem.',
    SERVER_BUSY_RETRY:'Máy chủ đang xử lý một yêu cầu dữ liệu khác. Vui lòng thử lại sau ít giây.',
    GATEWAY_UNAVAILABLE:'Máy chủ tạm thời chưa kết nối được. Dữ liệu đã tải vẫn được giữ.',
    PERSISTENT_STORAGE_UNAVAILABLE:'Kho ghi chú tạm thời chưa kết nối được. Chưa xác nhận được kết quả lưu; hãy kiểm tra lại trước khi lưu tiếp.',
    PERSISTENT_REVIEWS_NOT_CONFIGURED:'Chức năng lưu ghi chú chưa được cấu hình trên máy chủ.',
    INVALID_RESPONSE:'Phản hồi dữ liệu không hợp lệ. Vui lòng tải lại trang.'
  };
  function failure(code,status){const e=Error(messages[code]||`Không đọc/lưu được dữ liệu (${status}). Vui lòng thử lại sau.`);e.code=code;e.status=status;return e;}
  function create({fetch:fetcher,wait=ms=>new Promise(r=>setTimeout(r,ms)),onWaiting=()=>{}}){
    const pending=new Map(),waiting=new Map();
    const announce=()=>onWaiting([...waiting.values()].at(-1)||null);
    async function perform(path,options,readOnly,key){
      try{
        for(let attempt=0;;attempt++){
          let response,body,status=502,code='GATEWAY_UNAVAILABLE';
          try{response=await fetcher(path,{cache:'no-store',...options});status=response.status;if(response.ok)return response.json();}
          catch(e){if(!readOnly)throw failure('GATEWAY_UNAVAILABLE',0);}
          if(response){
            try{body=await response.json();}catch(e){body=null;}
            code=typeof body?.error==='string'&&/^[A-Z0-9_]{1,100}$/.test(body.error)?body.error:([502,503,504].includes(status)?'GATEWAY_UNAVAILABLE':'HTTP_ERROR');
          }
          const transient=[429,502,503,504].includes(status);
          const max=code==='DATA_RESTORING_RETRY'?60:code==='SERVER_BUSY_RETRY'?12:8;
          if(!readOnly||!transient||attempt>=max)throw failure(code,status);
          waiting.set(key,{path,code,status,attempt:attempt+1});announce();
          const hint=Number(response?.headers?.get?.('Retry-After'));
          const delay=Number.isFinite(hint)&&hint>0?Math.min(10000,hint*1000):code==='SERVER_BUSY_RETRY'?Math.min(5000,500+attempt*500):3000;
          await wait(delay);
        }
      }finally{waiting.delete(key);announce();}
    }
    function request(path,options={}){
      const readOnly=!options.method||options.method==='GET',key=path;
      if(readOnly&&pending.has(key))return pending.get(key);
      const result=perform(path,options,readOnly,key);
      if(!readOnly)return result;
      const tracked=result.finally(()=>pending.delete(key));pending.set(key,tracked);return tracked;
    }
    return {request};
  }
  return {create};
})();
if(typeof module!=='undefined')module.exports=MonitorHTTP;
