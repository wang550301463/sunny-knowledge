/** Existing transport owns origins, bearer/workload identity, deadlines and live ACL. */
export interface Operation {
 readonly id: string; readonly service: string; readonly method: string; readonly template: string;
 readonly path_parameters: readonly string[]; readonly query_parameters: readonly string[];
 readonly header_parameters: readonly string[]; readonly body_required: boolean; readonly accepts_body: boolean;
 readonly response_typing: string; readonly transport: string;
 readonly request_schema: string | null; readonly response_schema: string | null;
}
export interface Prepared {
 readonly service: string; readonly method: string; readonly path: string;
 readonly body: string | undefined; readonly headers: Readonly<Record<string,string>>;
}
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export function prepare(operation: Operation, input: {path?: Record<string,string>; query?: Record<string,string|number|boolean|readonly string[]>; body?:JsonValue; headers?:Record<string,string>} = {}): Prepared {
 const path=input.path ?? {}, query=input.query ?? {}, headers=input.headers ?? {};
 if (Object.keys(path).length !== operation.path_parameters.length) throw new Error('Exact path parameters required');
 let target=operation.template;
 for (const key of operation.path_parameters) {
  const value=path[key]; if (!value || value==='.' || value==='..') throw new Error('Literal path segment required');
  target=target.replaceAll('{'+key+'}',encodeURIComponent(value).replace(/[!'()*]/g,c=>'%'+c.charCodeAt(0).toString(16).toUpperCase()));
 }
 if (!target.startsWith('/') || target.startsWith('//') || target.includes('{')) throw new Error('Relative service path required');
 const values=new URLSearchParams();
 for (const key of Object.keys(query).sort()) {
  if (!operation.query_parameters.includes(key)) throw new Error('Unknown query parameter');
  const value=query[key]; for (const item of Array.isArray(value)?value:[value]) values.append(key,String(item));
 }
 if (values.size) target+='?'+values.toString();
 const allowed=new Set(operation.header_parameters.map(name=>name.toLowerCase()));
 for (const [key,value] of Object.entries(headers)) if (!allowed.has(key.toLowerCase()) || ['authorization','x-service-token','cookie','host'].includes(key.toLowerCase()) || /[\r\n]/.test(key+value)) throw new Error('Identity and undeclared headers are transport-owned');
 if ((operation.body_required && input.body===undefined) || (!operation.accepts_body && input.body!==undefined)) throw new Error('Invalid request body');
 const body=input.body===undefined?undefined:JSON.stringify(input.body,(_key,value:unknown)=>{if(typeof value==='number'&&!Number.isFinite(value)) throw new Error('JSON requires finite numbers');return value;});
 return Object.freeze({service:operation.service,method:operation.method,path:target,body,headers:Object.freeze({...headers})});
}