'use strict';
/* Plotting only. Geometry and source-time guards come from the shared Python model. */
const DepthSurface=(()=>{
  let loader;
  const versions=new WeakMap();
  function load(){
    if(globalThis.Plotly)return Promise.resolve(globalThis.Plotly);
    if(!loader)loader=new Promise((resolve,reject)=>{
      const script=document.createElement('script');
      script.src=new URL('./vendor/plotly-gl3d-3.7.0.min.js',document.baseURI).href;
      script.onload=()=>resolve(globalThis.Plotly);
      script.onerror=()=>{loader=null;script.remove();reject(new Error('Không tải được thư viện 3D. Hãy thử tải lại.'));};
      document.head.appendChild(script);
    });
    return loader;
  }
  function clear(element,text='Chưa có điểm dữ liệu để vẽ độ sâu 3D.'){
    versions.set(element,(versions.get(element)||0)+1);
    if(globalThis.Plotly)globalThis.Plotly.purge(element);
    element.textContent=text;
  }
  async function render(element,model){
    if(!model?.points){clear(element);return;}
    const version=(versions.get(element)||0)+1;versions.set(element,version);
    if(!globalThis.Plotly)element.textContent='Đang tải biểu đồ 3D…';
    try{
      const plotly=await load();
      if(versions.get(element)!==version)return;
      if(!element.classList.contains('js-plotly-plot'))element.replaceChildren();
      await plotly.react(element,model.figure.data,{...model.figure.layout,autosize:true},
        {responsive:true,scrollZoom:true,displaylogo:false,toImageButtonOptions:{format:'png',filename:'liquiditylab-depth-3d',scale:2}});
    }catch(error){if(versions.get(element)===version)clear(element,error.message);}
  }
  return {render,clear};
})();
