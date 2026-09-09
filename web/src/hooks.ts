import { useCallback, useEffect, useRef, useState } from 'react';
export function useResource<T>(key:string,loader:(signal:AbortSignal)=>Promise<T>) {
  const loaderRef=useRef(loader);loaderRef.current=loader;
  const [generation,setGeneration]=useState(0);
  const [result,setResult]=useState<{key:string;generation:number;data?:T;error?:unknown;loading:boolean}>({key,generation,loading:true});
  useEffect(()=>{const controller=new AbortController();let live=true;setResult({key,generation,loading:true});void loaderRef.current(controller.signal).then(data=>{if(live)setResult({key,generation,data,loading:false});},error=>{if(live)setResult({key,generation,error,loading:false});});return()=>{live=false;controller.abort();};},[key,generation]);
  const refresh=useCallback(()=>setGeneration(value=>value+1),[]);
  // Never display a previous space or a pre-revocation value during a new request.
  return {...(result.key===key&&result.generation===generation?result:{loading:true,data:undefined,error:undefined}),refresh};
}
export function useMutation() {const [busy,setBusy]=useState(false),[error,setError]=useState<unknown>();return {busy,error,clearError:()=>setError(undefined),run:async<T>(operation:()=>Promise<T>,done?:(value:T)=>void)=>{setBusy(true);setError(undefined);try{const value=await operation();done?.(value);return value;}catch(error){setError(error);return undefined;}finally{setBusy(false);}}};}