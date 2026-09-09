import { ApiError } from "./api";
import type { BindingProof, ChannelConfig, ChannelGroup, ChannelIdentity, ChannelInput, ChannelMessage } from "./channel-types";
function object(v: unknown): Record<string, unknown> {
  if (!v || typeof v !== "object" || Array.isArray(v)) throw new ApiError(502, "invalid_channel_response");
  return v as Record<string, unknown>;
}
function str(v: unknown): string { if (typeof v !== "string") throw new ApiError(502,"invalid_channel_response"); return v; }
function int(v: unknown): number { if (!Number.isSafeInteger(v) || (v as number)<0) throw new ApiError(502,"invalid_channel_response");return v as number; }
function bool(v: unknown): boolean { if(typeof v!=="boolean")throw new ApiError(502,"invalid_channel_response");return v; }
function strings(v:unknown):string[]{if(!Array.isArray(v))throw new ApiError(502,"invalid_channel_response");return v.map(str);}
// Pick response fields explicitly: a malformed server response must not retain credentials.
export function channelConfig(raw:unknown):ChannelConfig {const v=object(raw);return {id:str(v.id),name:str(v.name),bot_id:str(v.bot_id),agent_id:str(v.agent_id),agent_configuration_id:str(v.agent_configuration_id),space_ids:strings(v.space_ids),version:int(v.version),enabled:bool(v.enabled),status:str(v.status),tested_version:int(v.tested_version),secret_configured:bool(v.secret_configured)};}
export function channelGroup(raw:unknown):ChannelGroup {const v=object(raw);if(v.sync_state!=="pending"&&v.sync_state!=="synced")throw new ApiError(502,"invalid_group_state");return {id:str(v.id),channel_id:str(v.channel_id),chat_id:str(v.chat_id),audience_id:str(v.audience_id),space_ids:strings(v.space_ids),version:int(v.version),enabled:bool(v.enabled),audience_version:int(v.audience_version),desired_enabled:bool(v.desired_enabled),sync_state:v.sync_state,sync_error:str(v.sync_error??"")};}
export function channelIdentity(raw:unknown):ChannelIdentity {const v=object(raw);return {channel_id:str(v.channel_id),external_user_id:str(v.external_user_id),version:int(v.version),active:bool(v.active)};}
export function channelMessage(raw:unknown):ChannelMessage {const v=object(raw);return {message_id:str(v.message_id),state:str(v.state),run_id:str(v.run_id??""),created_at:str(v.created_at)};}
export function channelList<T>(raw:unknown,decode:(v:unknown)=>T):T[]{const v=object(raw);if(!Array.isArray(v.items))throw new ApiError(502,"invalid_channel_response");return v.items.map(decode);}
export function configInput(config:ChannelConfig,enabled:boolean):ChannelInput {return {base_version:config.version,name:config.name,bot_id:config.bot_id,bot_secret:"",agent_id:config.agent_id,space_ids:[...config.space_ids],enabled};}
export function groupState(group:Pick<ChannelGroup,"sync_state"|"enabled"|"desired_enabled">){
 if(group.sync_state==="pending")return {label:"授权同步待完成，当前禁用",usable:false};
 const usable=group.sync_state==="synced"&&group.enabled&&group.desired_enabled;
 return {label:usable?"已授权群聊":group.sync_state==="synced"?"已禁用":"状态未验证",usable};
}
export function connectionState(c:ChannelConfig):string {
 const states:Record<string,string>={disabled:"已停用",test_pending:"正在检测连接",tested:"连接检测通过",connecting:"正在连接",reconnecting:"正在重连",connected:"已连接",auth_failed:"机器人凭据验证失败",displaced:"连接被替代，已隔离",connection_failed:"连接失败"};
 return Object.hasOwn(states,c.status)?states[c.status]:"连接状态未验证";
}
export function canEnable(c:ChannelConfig){return !c.enabled&&c.secret_configured&&c.version===c.tested_version&&["disabled","tested"].includes(c.status);}
export const channelKeyPattern=/^[A-Za-z0-9_.:-]{1,256}$/;
export function parseBindingLink(hash:string):BindingProof|undefined {
 if(!hash)return undefined;
 const p=new URLSearchParams(hash.replace(/^#/,""));
 const keys=[...p.keys()];const id=p.get("challenge"),token=p.get("token");
 if(keys.length!==2||!keys.includes("challenge")||!keys.includes("token")||!id||!channelKeyPattern.test(id)||!token||!/^[-A-Za-z0-9_]{43}$/.test(token))throw new ApiError(400,"invalid_binding_link");
 return {challenge_id:id,web_token:token};
}
export function confirmationCommand(raw:unknown,id:string):string {const command=str(object(raw).confirmation_command);const parts=command.split(" ");if(parts.length!==3||parts[0]!=="/确认"||parts[1]!==id||!/^[-A-Za-z0-9_]{43}$/.test(parts[2]))throw new ApiError(502,"invalid_binding_response");return command;}
