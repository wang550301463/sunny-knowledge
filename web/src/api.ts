export class ApiError extends Error {
  constructor(public status:number, public code:string, public requestId?:string) { super(code); }
}
export function errorMessage(error:unknown):string {
  if (error instanceof ApiError) {
    if (error.status===401) return '登录已失效，请重新登录。';
    if (error.status===403) return '权限不足：需要当前空间授权或相应管理权限与访问范围。';
    if (error.status===409) return '版本已变化或记录冲突。请刷新并比较最新版本后重新提交；未覆盖任何内容。';
    if (error.status===404) return '资源不存在、不可访问，或此服务尚未接入。';
    if (error.status===422||error.status===400) return '提交内容不符合接口要求，请检查字段、证据和版本。';
    if (error.code==='authorization_changed') return '授权在读取期间发生变化，已停止展示。请刷新后重试。';
    if (error.status>=500||error.status===0) return '连接失败：服务或授权依赖暂不可用，请稍后重试。';
  }
  return '操作未完成，请重试。';
}
export function isAuthorizationFailure(error:unknown):boolean {
  return error instanceof ApiError && (error.status===401 || error.status===403 || error.code==='authorization_changed' || error.code==='unauthenticated');
}
export const pathId=(value:string)=>encodeURIComponent(value);
export function query(values:Record<string,string|number|undefined|null>) { const params=new URLSearchParams(); Object.entries(values).forEach(([key,value])=>{if(value!==undefined&&value!==null&&value!=='')params.set(key,String(value));}); return params.size ? `?${params}` : ''; }
export class ApiClient {
  // fetch must be invoked with the Window receiver; a bare method call
  // (this.fetcher(...)) throws "Illegal invocation" in browsers.
  constructor(private token:()=>Promise<string|null>,private fetcher:typeof fetch=fetch.bind(globalThis)){}
  async request<T>(path:string,init:RequestInit={}):Promise<T> {
    const response=await this.response(path,init);
    if(response.status===204)return undefined as T;
    try { return await response.json() as T; } catch { throw new ApiError(502,'invalid_response'); }
  }
  async response(path:string,init:RequestInit={}):Promise<Response> {
    if(!path.startsWith('/')||path.startsWith('//')||path.includes('://'))throw new ApiError(400,'invalid_path');
    let token:string|null=null;
    try { token=await this.token(); } catch(e) { console.error('[diag] token threw', path, e); throw new ApiError(401,'unauthenticated'); }
    if(!token){ console.error('[diag] token null', path); throw new ApiError(401,'unauthenticated'); }
    const headers=new Headers(init.headers); headers.set('Authorization',`Bearer ${token}`); headers.set('Accept',headers.get('Accept')??'application/json'); if(init.body)headers.set('Content-Type','application/json');
    let response:Response;
    try { response=await this.fetcher(`/api/v1${path}`,{...init,headers,credentials:'omit',cache:'no-store',redirect:'error',referrerPolicy:'no-referrer'}); }
    catch(error) { if(error instanceof DOMException&&error.name==='AbortError')throw error; console.error('[diag] fetch threw', path, error); throw new ApiError(0,'network_error'); }
    if(!response.ok) { let code='request_failed'; try { const body=await response.json(); if(typeof body?.error?.code==='string')code=body.error.code; }catch{/* Gateway may return a non-JSON error. */} console.error('[diag] http', response.status, path); throw new ApiError(response.status,code,response.headers.get('X-Request-ID')??undefined); }
    return response;
  }
  get<T>(path:string,signal?:AbortSignal){return this.request<T>(path,{signal});}
  post<T>(path:string,body:unknown){return this.request<T>(path,{method:'POST',body:JSON.stringify(body)});}
  put<T>(path:string,body?:unknown){return this.request<T>(path,{method:'PUT',...(body===undefined?{}:{body:JSON.stringify(body)})});}
  patch<T>(path:string,body:unknown){return this.request<T>(path,{method:'PATCH',body:JSON.stringify(body)});}
  delete<T>(path:string,body?:unknown){return this.request<T>(path,{method:'DELETE',...(body===undefined?{}:{body:JSON.stringify(body)})});}
}