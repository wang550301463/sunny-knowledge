import { useEffect,useRef,useState } from "react";
import { Alert,Button,Input,List,Popconfirm,Space,Tag,Typography } from "antd";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { ApiError,pathId } from "../api";
import { channelIdentity,channelList,confirmationCommand,parseBindingLink } from "../channel-api";
import { useChannelResource } from "../channel-hooks";
import { ErrorNotice,PageHeader,RefreshButton,ResourceView } from "../components/Common";
export function ChannelBindingPage(){
 const {api,user}=useAuth();
 const [initial]=useState(()=>{try{return {proof:parseBindingLink(window.location.hash),error:undefined};}catch(error){return {proof:undefined,error};}});
 const proof=useRef(initial.proof),live=useRef(true);
 const [available,setAvailable]=useState(!!initial.proof),[busy,setBusy]=useState(false),[command,setCommand]=useState<string>(),[error,setError]=useState<unknown>(initial.error);
 useEffect(()=>{live.current=true;window.history.replaceState(window.history.state,"",window.location.pathname+window.location.search);return()=>{live.current=false;proof.current=undefined;};},[]);
 const claim=async()=>{const value=proof.current;if(!value||busy)return;proof.current=undefined;setAvailable(false);setBusy(true);setCommand(undefined);setError(undefined);try{const response=await api.post<unknown>("/channel-bindings/claim",value);if(live.current)setCommand(confirmationCommand(response,value.challenge_id));}catch(e){if(live.current){setCommand(undefined);setError(e);}}finally{if(live.current)setBusy(false);}};
 return <><PageHeader eyebrow="ACCOUNT CONNECTION" title="关联企业微信账号" description="使用当前平台账号认领，再回到同一企微账号的私聊完成确认。"/><div className="panel channel-binding-card">
  <Typography.Paragraph>当前平台账号：<strong>{String(user?.profile.name??user?.profile.preferred_username??"当前登录用户")}</strong></Typography.Paragraph>
  <Alert type="info" showIcon message="双向一次性确认" description="链接自企微生成起 5 分钟内有效，以服务端校验为准。请勿转发链接或确认命令；重新绑定会撤销旧关联与会话上下文。"/>
  {available?<Button type="primary" onClick={()=>void claim()} loading={busy}>确认关联当前账号</Button>:busy?<Typography.Paragraph>正在认领，尚未完成绑定…</Typography.Paragraph>:command?<>
   <Alert type="warning" showIcon message="等待企微私聊确认" description="复制下面完整命令，回到发起绑定的同一企微账号与机器人的私聊发送。仅企微确认回复表示关联完成。"/>
   <Input.TextArea aria-label="企微确认命令" value={command} readOnly autoSize autoComplete="off"/>
   <Button onClick={()=>setCommand(undefined)}>隐藏确认命令</Button>
  </>:!error?<Alert type="info" message="登录后，请回到企微重新打开同一条未过期的绑定链接。也可以在机器人私聊发送 /绑定 获取新链接。"/>:null}
  {error instanceof ApiError&&[400,403].includes(error.status)?<Alert type="error" message="绑定链接已失效、已使用或无法验证。请回企微私聊发送 /绑定 获取新链接。"/>:<ErrorNotice error={error}/>}
  {error&&<Typography.Paragraph>本次一次性凭据已清除。请求结果不确定时，请从企微重新发起绑定。</Typography.Paragraph>}
  <Link to="/channels/bindings">查看我的实际绑定状态</Link>
 </div></>;
}
export function MyChannelBindings(){
 const {api}=useAuth();const bindings=useChannelResource("my-channel-bindings",async signal=>channelList(await api.get("/channel-bindings",signal),channelIdentity));
 const [busy,setBusy]=useState<string>(),[error,setError]=useState<unknown>();const live=useRef(true);
 useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
 const unbind=async(channel:string,external:string)=>{if(busy)return;setBusy(channel+external);setError(undefined);try{await api.delete(`/channels/${pathId(channel)}/bindings/${pathId(external)}`);}catch(e){if(live.current)setError(e);}finally{if(live.current){setBusy(undefined);bindings.refresh();}}};
 return <><PageHeader title="我的企微绑定" description="这里只显示当前平台账号的关联。解除后，该企微账号与旧会话立即失去平台访问权限。" extra={<RefreshButton onClick={bindings.refresh}/>}/><Alert type="info" showIcon message="新增或重新绑定" description="在企微机器人私聊发送 /绑定，打开登录链接并完成双向确认。"/><ErrorNotice error={error}/><ResourceView resource={bindings}>{items=><List className="panel" dataSource={items} locale={{emptyText:"当前账号尚无企微绑定"}} renderItem={item=><List.Item actions={item.active?[<Popconfirm key="unbind" title="解除这个企微账号的绑定？" description="旧会话将不可继续使用；重新关联需要双向确认。" okText="确认解除" cancelText="取消" onConfirm={()=>unbind(item.channel_id,item.external_user_id)}><Button danger loading={busy===item.channel_id+item.external_user_id} disabled={!!busy} aria-label={`解除绑定 ${item.external_user_id}`}>解除绑定</Button></Popconfirm>]:[]}><List.Item.Meta title={<Space>{item.external_user_id}<Tag color={item.active?"green":undefined}>{item.active?"已绑定":"已解除"}</Tag></Space>} description={<>渠道 {item.channel_id} · 绑定版本 {item.version}</>}/></List.Item>}/>}</ResourceView></>;
}
