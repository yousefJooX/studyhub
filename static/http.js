(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.StudyHttp=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  class HttpError extends Error {
    constructor(status,message){super(message);this.name='HttpError';this.status=status;}
  }
  class ConnectionError extends Error {
    constructor(uncertainWrite=false){
      super(uncertainWrite?'الاتصال انقطع أثناء الحفظ. حدّث البيانات واتأكد هل اتحفظت قبل إعادة المحاولة.':'الاتصال بالموقع مش متاح دلوقتي. راجع الإنترنت وحاول تاني.');
      this.name='ConnectionError';this.uncertainWrite=uncertainWrite;
    }
  }
  async function json(url,options={},dependencies={}){
    const method=(options.method||'GET').toUpperCase(), readable=method==='GET';
    const fetcher=dependencies.fetcher||((url,options)=>globalThis.fetch(url,options));
    const sleep=dependencies.sleep||(ms=>new Promise(resolve=>setTimeout(resolve,ms)));
    const attempts=readable?3:1;
    for(let attempt=0;attempt<attempts;attempt++){
      const controller=new AbortController();
      const timeout=setTimeout(()=>controller.abort(),dependencies.timeoutMs??(readable?10000:20000));
      let error;
      try{
        const response=await fetcher(url,{...options,method,signal:controller.signal});
        let result;
        try{result=await response.json();}
        catch{if(response.ok)throw new ConnectionError(!readable);result={};}
        if(!response.ok)throw new HttpError(response.status,typeof result?.detail==='string'?result.detail:response.status>=500?(readable?'الموقع مش متاح مؤقتًا. حاول تاني بعد لحظات.':'تعذّر تأكيد الحفظ. حدّث البيانات واتأكد هل اتحفظت قبل إعادة المحاولة.'):'راجع البيانات وحاول تاني');
        return result;
      }catch(caught){
        error=caught instanceof HttpError||caught instanceof ConnectionError?caught:new ConnectionError(!readable);
      }finally{clearTimeout(timeout);}
      const transient=error instanceof ConnectionError||[502,503,504].includes(error.status);
      if(!readable||!transient||attempt===attempts-1)throw error;
      dependencies.onRetry?.(attempt+1,attempts);
      await sleep(attempt===0?500:1500);
    }
  }
  return {json,HttpError,ConnectionError};
});
