import { useCallback,useEffect,useRef,useState } from "react";
// Automatic refreshes share an in-flight read. Focus/manual refresh still clears
// cached metadata and fences late results after a permission or context change.
export function useChannelResource<T>(key:string,loader:(signal:AbortSignal)=>Promise<T>,interval=5000){
 const latest=useRef(loader);latest.current=loader;
 const [generation,setGeneration]=useState(0);
 const [state,setState]=useState<{key:string;generation:number;data?:T;loading:boolean;error?:unknown}>({key,generation,loading:true});
 useEffect(()=>{let live=true;let pending=false;const controller=new AbortController();
  const load=async()=>{if(pending||!live)return;pending=true;try{const data=await latest.current(controller.signal);if(live)setState({key,generation,data,loading:false});}catch(error){if(live)setState({key,generation,error,loading:false});}finally{pending=false;}};
  setState({key,generation,loading:true});void load();
  const timer=interval?setInterval(()=>void load(),interval):undefined;
  const focus=()=>setGeneration(g=>g+1);window.addEventListener("focus",focus);
  return()=>{live=false;controller.abort();clearInterval(timer);window.removeEventListener("focus",focus);};
 },[key,generation,interval]);
 const refresh=useCallback(()=>setGeneration(g=>g+1),[]);
 return {...(state.key===key&&state.generation===generation?state:{loading:true,data:undefined,error:undefined}),refresh};
}
