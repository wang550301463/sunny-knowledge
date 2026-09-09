import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Alert, Button, Card, Checkbox, Collapse, Descriptions, Drawer, Empty, Form, Input, InputNumber, Select, Space, Tag, Typography } from "antd";
import { EditOutlined, ExperimentOutlined, PlusOutlined, RobotOutlined } from "@ant-design/icons";
import { useAuth } from "../auth";
import { pathId, query } from "../api";
import { modeLabels, readableRun, toolLabels } from "../agent-api";
import { useAgentList } from "../agent-hooks";
import { ErrorNotice, PageHeader, ResourceView } from "../components/Common";
import { useResource } from "../hooks";
import type { AgentCreate, AgentDefinition, AgentModel, AgentPreset, AgentRun, AgentSession, AgentTool } from "../agent-types";
import type { Space as KnowledgeSpace } from "../types";
import { RunPanel } from "../components/AgentRun";

const budgets={model_rounds:8,tool_calls:20,parallel_reads:2,seconds:180};
const initial=(preset?:AgentPreset):AgentCreate=>({name:preset?.name??"",description:"",owner_space_id:"",config:{mode:preset?.id??"knowledge_qa",model_configuration_id:null,prompt:preset?.instruction??"",space_ids:[],tools:preset?.tools??["search","get"],tool_space_ids:{},budget:budgets,max_output_tokens:2048}});

export function AgentsPage() {
 const [params,setParams]=useSearchParams();
 const agents=useAgentList<AgentDefinition>("/agents"),presets=useAgentList<AgentPreset>("/agents/presets"),models=useAgentList<AgentModel>("/agents/models"),spaces=useAgentList<KnowledgeSpace>("/spaces");
 const [create,setCreate]=useState<AgentCreate|null>(null);
 const agentId=params.get("agent")??"";
 return <>
  <PageHeader eyebrow="AGENTS" title="智能体" description="把模型、知识范围和工具配置成可复用的研发助手。每次运行都重新检查使用者的权限。" extra={<Button type="primary" aria-label="创建智能体" icon={<PlusOutlined/>} onClick={()=>setCreate(initial())}>创建智能体</Button>}/>
  <ErrorNotice error={agents.error} retry={()=>void agents.refresh()}/><ErrorNotice error={presets.error}/><ErrorNotice error={models.error}/><ErrorNotice error={spaces.error}/>
  <Typography.Title level={4}>从预设开始</Typography.Title>
  <div className="agent-preset-grid">{presets.items.map(preset=><Card key={preset.id} className="agent-preset" size="small"><RobotOutlined/><Typography.Title level={5}>{preset.name}</Typography.Title><Typography.Paragraph ellipsis={{rows:3}} type="secondary">{preset.instruction}</Typography.Paragraph><Space wrap>{preset.tools.map(tool=><Tag key={tool}>{toolLabels[tool]}</Tag>)}</Space><Button block onClick={()=>setCreate(initial(preset))}>使用此预设</Button></Card>)}</div>
  {!models.loading&&!models.error&&models.items.length===0&&<Alert type="warning" showIcon message="还没有可供智能体使用的 Chat 模型" description={<Link to="/settings/models">添加并测试模型，然后回来完成配置</Link>}/>}
  <div className="agent-section-heading"><Typography.Title level={4}>我的与共享智能体</Typography.Title><Button onClick={()=>void agents.refresh()}>刷新列表</Button></div>
  {!agents.loading&&!agents.error&&agents.items.length===0&&<Empty description="尚无可访问的智能体，选择预设或创建一个。"/>}
  <div className="agent-definition-grid">{agents.items.map(agent=><Card key={agent.id} title={agent.name} extra={<Tag color={agent.shared?"green":"default"}>{agent.shared?"共享":"私有草稿"}</Tag>}><Typography.Paragraph type="secondary">{agent.description||modeLabels[agent.config.mode]}</Typography.Paragraph><Space wrap><Tag>{modeLabels[agent.config.mode]}</Tag><Tag>配置 v{agent.version}</Tag><Tag>{agent.config.space_ids.length} 个空间</Tag>{!agent.config.model_configuration_id&&<Tag color="warning">待选模型</Tag>}{agent.published_configuration_id&&agent.published_configuration_id!==agent.configuration_id&&<Tag color="gold">有未发布修改</Tag>}</Space><div className="agent-card-actions"><Button aria-label={`配置与发布 ${agent.name}`} icon={<EditOutlined/>} onClick={()=>setParams({agent:agent.id})}>配置与发布</Button><Link to={`/chat${query({agent:agent.id,configuration:agent.configuration_id})}`}>开始问答</Link></div></Card>)}</div>
  {agents.next&&<Button onClick={()=>void agents.more()}>加载更多智能体</Button>}
  {agentId&&<AgentWorkspace key={agentId} id={agentId} models={models.items} spaces={spaces.items} modelsMore={models.next?models.more:undefined} spacesMore={spaces.next?spaces.more:undefined} onClose={()=>setParams({})} onChanged={()=>void agents.refresh()}/>}
  {create&&<AgentEditor key="create" initialValue={create} models={models.items} spaces={spaces.items} modelsMore={models.next?models.more:undefined} spacesMore={spaces.next?spaces.more:undefined} onClose={()=>setCreate(null)} onSaved={agent=>{setCreate(null);setParams({agent:agent.id});void agents.refresh()}}/>}
 </>;
}

interface Choices {models:AgentModel[];spaces:KnowledgeSpace[];modelsMore?:()=>Promise<void>;spacesMore?:()=>Promise<void>}
function AgentEditor({initialValue,existing,models,spaces,modelsMore,spacesMore,onClose,onSaved}:Choices&{initialValue:AgentCreate;existing?:AgentDefinition;onClose:()=>void;onSaved:(agent:AgentDefinition)=>void}) {
 const {api}=useAuth();const [form]=Form.useForm<AgentCreate>();const [busy,setBusy]=useState(false),[error,setError]=useState<unknown>();
 const live=useRef(true);useEffect(()=>()=>{live.current=false},[]);
 const scope=Form.useWatch(["config","space_ids"],form) as string[]|undefined;
 const enabledTools=Form.useWatch(["config","tools"],form) as AgentTool[]|undefined;
 const save=async(values:AgentCreate)=>{setBusy(true);setError(undefined);try{
  const config={...values.config,model_configuration_id:values.config.model_configuration_id||null,tool_space_ids:Object.fromEntries(Object.entries(values.config.tool_space_ids??{}).filter(([tool])=>values.config.tools.includes(tool as AgentTool)))};
  const result=existing?await api.put<AgentDefinition>(`/agents/${pathId(existing.id)}`,{base_configuration_id:existing.configuration_id,name:values.name.trim(),description:values.description??"",config}):await api.post<AgentDefinition>("/agents",{...values,name:values.name.trim(),description:values.description??"",config});
  if(live.current)onSaved(result);
 }catch(err){if(live.current)setError(err)}finally{if(live.current)setBusy(false)}};
 return <Drawer title={existing?`编辑 ${existing.name} · 基于 v${existing.version}`:"创建智能体"} open width={740} onClose={onClose} destroyOnHidden extra={<Button type="primary" aria-label="保存配置" loading={busy} onClick={()=>form.submit()}>保存配置</Button>}>
  <Typography.Paragraph type="secondary">保存会生成独立配置版本。共享发布需要空间授权管理权限，使用者仍受自己的数据权限约束。</Typography.Paragraph>
  <ErrorNotice error={error}/>
  <Form form={form} layout="vertical" initialValues={initialValue} onFinish={save}>
   <div className="form-grid"><Form.Item label="名称" name="name" rules={[{required:true,whitespace:true,max:160}]}><Input maxLength={160}/></Form.Item><Form.Item label="归属空间" name="owner_space_id" rules={[{required:true}]}><Select disabled={!!existing} options={spaces.map(s=>({value:s.id,label:s.name}))}/></Form.Item></div>
   <Form.Item label="说明" name="description"><Input.TextArea rows={2} maxLength={1000}/></Form.Item>
   <div className="form-grid"><Form.Item label="运行模式" name={["config","mode"]} rules={[{required:true}]}><Select options={Object.entries(modeLabels).map(([value,label])=>({value,label}))}/></Form.Item><Form.Item label="Chat 模型" name={["config","model_configuration_id"]} extra="使用固定模型配置版本；可先保存未选模型的草稿。"><Select allowClear placeholder="选择模型" options={models.map(m=>({value:m.configuration_id,label:`${m.name} · ${m.provider_model} · ${m.test_state==="passed"?"已测试":"待测试"}`}))}/></Form.Item></div>
   {modelsMore&&<Button onClick={()=>void modelsMore()}>加载更多模型</Button>}
   <Form.Item label="知识范围" name={["config","space_ids"]} rules={[{required:true,type:"array",min:1,max:100}]}><Select mode="multiple" options={spaces.map(s=>({value:s.id,label:s.name}))}/></Form.Item>
   {spacesMore&&<Button onClick={()=>void spacesMore()}>加载更多空间</Button>}
   <Form.Item label="可用工具" name={["config","tools"]}><Checkbox.Group options={Object.entries(toolLabels).map(([value,label])=>({value,label}))}/></Form.Item>
   <Collapse items={[{key:"advanced",label:"高级配置：指令、工具范围与运行预算",children:<>
    <Form.Item label="补充指令" name={["config","prompt"]}><Input.TextArea rows={5} maxLength={8000} showCount/></Form.Item>
    {(enabledTools??[]).map(tool=><Form.Item key={tool} label={`${toolLabels[tool]}的知识范围`} name={["config","tool_space_ids",tool]} extra="不单独设置时使用智能体范围；选定后只能进一步收紧。" rules={[{validator:(_,value:string[]|undefined)=>!value||value.every(id=>(scope??[]).includes(id))?Promise.resolve():Promise.reject(new Error("工具范围必须属于智能体范围"))}]}><Select allowClear mode="multiple" options={spaces.filter(s=>(scope??[]).includes(s.id)).map(s=>({value:s.id,label:s.name}))}/></Form.Item>)}
    <div className="form-grid">{([{key:"model_rounds",label:"最多决策轮数",max:8},{key:"tool_calls",label:"最多工具调用",max:20},{key:"parallel_reads",label:"并行只读调用",max:2},{key:"seconds",label:"最长运行秒数",max:180}] as const).map(field=><Form.Item key={field.key} label={field.label} name={["config","budget",field.key]} rules={[{required:true,type:"number",min:1,max:field.max}]}><InputNumber min={1} max={field.max} precision={0}/></Form.Item>)}</div>
    <Form.Item label="最大回答 token 数" name={["config","max_output_tokens"]} rules={[{required:true,type:"number",min:128,max:8192}]}><InputNumber min={128} max={8192} precision={0}/></Form.Item>
   </>}]}/>
  </Form>
 </Drawer>;
}

function AgentWorkspace({id,models,spaces,modelsMore,spacesMore,onClose,onChanged}:Choices&{id:string;onClose:()=>void;onChanged:()=>void}) {
 const {api}=useAuth();const [version,setVersion]=useState<string>(),[edit,setEdit]=useState<AgentDefinition|null>(null),[test,setTest]=useState<AgentDefinition|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>();
 const current=useResource(`agent:${id}:${version??"current"}`,signal=>api.get<AgentDefinition>(`/agents/${pathId(id)}${query({configuration_id:version})}`,signal));
 const versions=useAgentList<AgentDefinition>(`/agents/${pathId(id)}/versions`);
 const live=useRef(true);useEffect(()=>()=>{live.current=false},[]);
 useEffect(()=>{const refresh=()=>current.refresh();window.addEventListener("focus",refresh);return()=>window.removeEventListener("focus",refresh)},[current.refresh]);
 const publish=async(agent:AgentDefinition,shared:boolean)=>{setBusy(true);setError(undefined);try{await api.post(`/agents/${pathId(id)}/publish`,{base_configuration_id:agent.configuration_id,shared});if(live.current){current.refresh();void versions.refresh();onChanged()}}catch(err){if(live.current)setError(err)}finally{if(live.current)setBusy(false)}};
 return <Drawer open title="智能体配置与发布" width={780} onClose={onClose} destroyOnHidden>
  <ErrorNotice error={error}/><ResourceView resource={current}>{agent=><>
   <Typography.Title level={3}>{agent.name}</Typography.Title><Typography.Paragraph type="secondary">{agent.description}</Typography.Paragraph>
   <Space wrap><Tag>{modeLabels[agent.config.mode]}</Tag><Tag>查看 v{agent.version}</Tag><Tag color={agent.shared?"green":"default"}>{agent.shared?"已共享":"未共享"}</Tag></Space>
   <Descriptions column={1} size="small" className="agent-config-description" items={[{key:"id",label:"配置版本",children:<code>{agent.configuration_id}</code>},{key:"published",label:"共享发布版本",children:agent.published_configuration_id??"未发布"},{key:"model",label:"模型配置",children:models.find(m=>m.configuration_id===agent.config.model_configuration_id)?.name??agent.config.model_configuration_id??"未选择"},{key:"scope",label:"知识范围",children:<Space wrap>{agent.config.space_ids.map(s=><Tag key={s}>{spaces.find(v=>v.id===s)?.name??s}</Tag>)}</Space>},{key:"tools",label:"工具",children:agent.config.tools.map(t=>toolLabels[t]).join("、")||"未启用工具"}]}/>
   <Space wrap className="agent-workspace-actions"><Button icon={<EditOutlined/>} onClick={()=>setEdit(agent)}>编辑此版本</Button><Button aria-label="发布当前版本" type="primary" loading={busy} onClick={()=>void publish(agent,true)}>发布当前版本</Button>{agent.shared&&<Button disabled={busy} onClick={()=>void publish(agent,false)}>停止共享</Button>}<Button icon={<ExperimentOutlined/>} onClick={()=>setTest(agent)} disabled={!agent.config.model_configuration_id}>测试此配置</Button></Space>
   <Alert type="info" showIcon message="发布按当前基础版本校验" description="出现版本冲突时请查看最新版本并比较，再决定是否提交。共享智能体不会扩大内容权限。"/>
   <Collapse items={[{key:"details",label:"指令与预算",children:<><Typography.Paragraph className="agent-prompt">{agent.config.prompt||"没有补充指令"}</Typography.Paragraph><Typography.Paragraph>最多 {agent.config.budget.model_rounds} 轮、{agent.config.budget.tool_calls} 次工具调用、{agent.config.budget.parallel_reads} 个并行只读调用、{agent.config.budget.seconds} 秒；回答上限 {agent.config.max_output_tokens} tokens。</Typography.Paragraph></>}]} />
   <Typography.Title level={4}>配置版本</Typography.Title><ErrorNotice error={versions.error}/><Select aria-label="查看配置版本" value={agent.configuration_id} style={{width:"100%"}} options={versions.items.map(v=>({value:v.configuration_id,label:`v${v.version} · ${v.name}${v.configuration_id===agent.published_configuration_id?" · 已发布":""}`}))} onChange={setVersion}/><Space><Button onClick={()=>{setVersion(undefined);current.refresh();void versions.refresh()}}>查看最新配置</Button>{versions.next&&<Button onClick={()=>void versions.more()}>更多版本</Button>}</Space>
  </>}</ResourceView>
  {edit&&<AgentEditor existing={edit} initialValue={{name:edit.name,description:edit.description,owner_space_id:edit.owner_space_id,config:edit.config}} models={models} spaces={spaces} modelsMore={modelsMore} spacesMore={spacesMore} onClose={()=>setEdit(null)} onSaved={agent=>{setEdit(null);setVersion(agent.configuration_id);current.refresh();void versions.refresh();onChanged()}}/>}
  {test&&<AgentTest key={test.configuration_id} agent={test} onClose={()=>setTest(null)}/>}
 </Drawer>;
}

function AgentTest({agent,onClose}:{agent:AgentDefinition;onClose:()=>void}) {
 const {api}=useAuth();const [question,setQuestion]=useState(""),[run,setRun]=useState<AgentRun>(),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>();const session=useRef<string|undefined>(undefined),attempt=useRef<{question:string;id:string}|undefined>(undefined);
 const live=useRef(true);useEffect(()=>()=>{live.current=false},[]);
 const submit=async()=>{setBusy(true);setError(undefined);try{if(!session.current)session.current=(await api.post<AgentSession>("/sessions",{title:`测试 · ${agent.name}`.slice(0,160)})).id;if(!live.current)return;if(attempt.current?.question!==question)attempt.current={question,id:crypto.randomUUID()};const result=readableRun(await api.post<AgentRun>("/runs",{agent_id:agent.id,configuration_id:agent.configuration_id,session_id:session.current,question:question.trim(),space_ids:agent.config.space_ids,idempotency_key:attempt.current.id}));if(live.current){setRun(result);attempt.current=undefined}}catch(err){if(live.current)setError(err)}finally{if(live.current)setBusy(false)}};
 return <Drawer title={`测试 ${agent.name} · v${agent.version}`} open onClose={onClose} width={800} destroyOnHidden><Alert type="info" showIcon message="使用固定配置版本进行真实运行" description="测试会使用实际模型和知识权限，运行记录保存在你的会话中。关闭面板不会停止运行。"/><Input.TextArea aria-label="测试问题" value={question} onChange={e=>setQuestion(e.target.value)} rows={3} maxLength={8192}/><Button loading={busy} disabled={!question.trim()} onClick={()=>void submit()}>开始测试</Button><ErrorNotice error={error}/>{run&&<><Link to={`/chat${query({session:run.session_id,run:run.id,agent:agent.id,configuration:agent.configuration_id})}`}>在问答页继续查看</Link><RunPanel key={run.id} runId={run.id} onRunChanged={setRun}/></>}</Drawer>;
}
