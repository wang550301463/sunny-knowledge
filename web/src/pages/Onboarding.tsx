import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Alert, Button, Card, Space, Steps, Tag, Typography } from "antd";
import { useAuth } from "../auth";
import { pathId, query } from "../api";
import { ErrorNotice, PageHeader } from "../components/Common";
import { emptySelection, onboardingChecks, readSelection } from "../onboarding-api";
import { useOnboarding } from "../onboarding-hooks";
import type { OnboardingChoice, OnboardingSelection } from "../onboarding-types";
import type { Principal } from "../types";
import "../onboarding.css";
const taskLabels: Record<string, string> = { queued: "排队中", running: "处理中", succeeded: "已完成", review_needed: "需要审核", superseded: "已被更新版本替代", failed: "失败" };
function Choice({ label, value, choices, disabled, onChange }: { label: string; value: string; choices: OnboardingChoice[]; disabled: boolean; onChange: (value: string) => void }) {
  return <label className="onboarding-field">{label}<select aria-label={label} value={choices.some(item => item.id === value) ? value : ""} disabled={disabled} onChange={event => onChange(event.target.value)}><option value="">请选择</option>{choices.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>;
}
export function OnboardingPage({ principal }: { principal: Principal }) {
  const { api } = useAuth(), [params, setParams] = useSearchParams();
  const storageKey = `sunny:onboarding:${principal.id}`;
  const [saved] = useState(() => {
    try { return readSelection(JSON.parse(sessionStorage.getItem(storageKey) ?? "{}")); }
    catch { return emptySelection; }
  });
  const selection = readSelection(params.size ? Object.fromEntries(params) : saved);
  const signature = JSON.stringify(selection);
  const resource = useOnboarding(api, selection), data = resource.data;
  const checks = onboardingChecks(data);
  useEffect(() => {
    // Only opaque selection IDs persist. Progress is always reconstructed from
    // fresh API evidence, including after a full-document return to the guide.
    const value = readSelection(JSON.parse(signature));
    try { sessionStorage.setItem(storageKey, JSON.stringify(value)); } catch { /* URL restoration remains usable. */ }
    const canonical = new URLSearchParams(Object.entries(value).filter(([, id]) => id));
    if (canonical.toString() !== params.toString()) setParams(canonical, { replace: true });
  }, [params, setParams, signature, storageKey]);
  function choose(key: keyof OnboardingSelection, value: string) {
    const next = { ...selection, [key]: value };
    if (key === "space") Object.assign(next, { source: "", page: "", run: "" });
    if (key === "source") Object.assign(next, { page: "", run: "" });
    if (key === "page" || key === "session") next.run = "";
    setParams(new URLSearchParams(Object.entries(next).filter(([, id]) => id)));
  }
  const steps = [
    { title: "模型能力", ready: checks.chat }, { title: "空间与授权", ready: checks.space },
    { title: "来源预览", ready: checks.preview }, { title: "同步与审核", ready: checks.sync },
    { title: "浏览 Wiki", ready: checks.wiki }, { title: "引用问答", ready: checks.answer },
  ];
  const status = (ready: boolean, pending: string) => <Tag color={ready ? "green" : "default"}>{ready ? "当前证据已核验" : pending}</Tag>;
  const sourcesHref = data?.space ? `/spaces/${pathId(data.space.id)}?tab=sources` : "/spaces";
  return <section className="onboarding-page" aria-label="首次使用引导">
    <PageHeader eyebrow="GET STARTED" title="首次使用" description="选一条真实知识链路，从来源到带引用回答。返回引导后会重新核验当前授权与状态。" extra={<Button onClick={resource.refresh} disabled={resource.suspended}>重新核验</Button>} />
    <Steps size="small" responsive items={steps.map((step, index) => ({ title: step.title, status: step.ready ? "finish" : "wait", onClick: () => document.getElementById(`onboarding-${index}`)?.scrollIntoView({ behavior: "smooth", block: "start" }) }))} />
    <Alert type="info" showIcon message="从对应页面完成操作，再通过页面顶部或设置中的“首次使用”返回。" description="仅保存本次选择的标识；所有完成状态重新读取服务器。不会自动给服务账号扩大权限，也不会替你发布知识或发起模型请求。" />
    {resource.suspended ? <Alert type="info" message="已暂停显示，返回此窗口后重新核验授权。" /> : resource.loading && !data ? <div role="status">正在核验当前用户与知识权限…</div> : null}
    <ErrorNotice error={resource.error} retry={resource.refresh} />
    <div className="onboarding-cards">
      <Card id="onboarding-0" title="1. 检查模型能力" extra={status(checks.chat, "等待可用 Chat 配置")}>
        <Typography.Paragraph>问答需要可用的 Chat、工具调用和流式输出能力。这里只采信当前活动配置的实际检测结果。</Typography.Paragraph>
        {data && (data.chatModels.length ? <ul>{data.chatModels.map(model => <li key={model.configuration}><strong>{model.name}</strong> <Tag color={model.tested ? "green" : "default"}>{model.tested ? "Chat / 工具 / 流式已检测" : "能力检测未完成或未通过"}</Tag>{model.simulated && <Tag color="orange">模拟协议模型</Tag>}</li>)}</ul> : <Alert type="warning" message="尚无可供当前用户选择的 Chat 模型" description="请平台管理员在模型服务中登记配置并检测 Chat、工具调用和流式能力。" />)}
        <Alert type="info" message="检索能力尚未独立验证" description="模型配置检测不代表检索运行时与索引已就绪；完成来源同步后再实际问答。带引用回答也不会被标成 Embedding 或 Reranker 的独立检测结果。" />
        {data?.admin && <><Typography.Paragraph>以下是当前已加载的管理配置，不代表检索运行时已绑定它们：</Typography.Paragraph><Space wrap>{(["embedding", "rerank"] as const).map(capability => <Tag key={capability}>{capability}: {data.adminModels?.some(model => model.capability === capability && model.tested) ? "存在已通过检测的配置" : "当前列表未发现已通过配置"}</Tag>)}</Space><p><Link to="/settings">配置与检测模型</Link></p></>}
        {data && !data.admin && <Typography.Paragraph type="secondary">你可以直接使用管理员提供的模型；此页不要求模型管理权限。缺少配置时请联系平台管理员。</Typography.Paragraph>}
      </Card>
      <Card id="onboarding-1" title="2. 选择空间与访问范围" extra={status(checks.space, "等待可读空间")}>
        <Choice label="本次知识空间" value={selection.space} choices={data?.spaces ?? []} disabled={!data} onChange={value => choose("space", value)} />
        <Typography.Paragraph>空间只有当前读取授权通过才算可用。平台管理员不会自动获得内容读取权。</Typography.Paragraph>
        <Space wrap><Link to="/spaces">创建或选择知识空间</Link>{data?.space && <Link to={`/spaces/${pathId(data.space.id)}?tab=settings`}>检查空间与来源授权</Link>}</Space>
        {data && !data.spaces.length && <Alert type="warning" message="没有当前可读取的空间" description={data.admin ? "创建空间后明确授予用户、部门或用户组所需权限，再继续本次链路。" : "请空间负责人授予读取权限；接入编辑与审核需要各自的独立授权。"} />}
      </Card>
      <Card id="onboarding-2" title="3. 添加来源并预览" extra={status(checks.preview, "等待当前版本预览")}>
        <Choice label="本次数据来源" value={selection.source} choices={data?.sources ?? []} disabled={!data?.space} onChange={value => choose("source", value)} />
        {data?.source && <Typography.Paragraph>配置 v{data.source.version} · {data.source.active ? "活动来源" : "已停用或删除"} · {data.source.previewRevision ? `${data.source.fileCount} 个文件已固定来源版本` : "尚未预览当前配置"}</Typography.Paragraph>}
        {data?.sourcesMore && <Typography.Paragraph type="secondary">此处显示最近一页来源。其他来源可在空间列表中处理。</Typography.Paragraph>}
        <Link to={sourcesHref}>添加来源、预览与同步</Link>
        <Typography.Paragraph type="secondary">通过真实 Git、Markdown 或工单表单预览原始快照，再显式启动同步；凭据只在对应表单内输入。</Typography.Paragraph>
      </Card>
      <Card id="onboarding-3" title="4. 同步与审核" extra={status(checks.sync, "任务尚未全部成功")}>
        {data?.task ? <><Typography.Paragraph>最新任务：{taskLabels[data.task.status] ?? "状态待核对"}{!data.task.currentVersion && " · 对应旧配置，需重新同步"}</Typography.Paragraph>{data.task.status === "review_needed" && <Alert type="warning" message="同步产生待审核内容" description="处理任务的状态不会被本地按钮改成成功。到审核页检查原文与修改；已单独发布的 Wiki 仍可在下一步浏览。" />}{data.task.error === "worker_authorization_required" && <Alert type="warning" message="接入 Worker 缺少所选空间或来源的授权" description="请授权负责人核对已登记的接入服务账号，仅为本次空间和来源授予所需读取、编辑权限。" />}{data.task.status === "failed" && data.task.error !== "worker_authorization_required" && <Alert type="error" message="真实同步任务失败，请在来源任务详情中检查原因。" />}</> : <Typography.Paragraph>尚未读取到所选来源的同步任务。预览成功后，在来源详情启动同步。</Typography.Paragraph>}
        <Space wrap><Link to={sourcesHref}>查看真实任务与失败详情</Link><Link to="/reviews">检查修订与审核</Link></Space>
      </Card>
      <Card id="onboarding-4" title="5. 浏览版本化 Wiki" extra={status(checks.wiki, "等待当前来源的正式知识")}>
        <Choice label="本次 Wiki" value={selection.page} choices={data?.task?.pages ?? []} disabled={!data?.task} onChange={value => choose("page", value)} />
        {data?.page && <><Typography.Paragraph><strong>{data.page.name}</strong> · {data.page.referenceCount} 条去重原始引用</Typography.Paragraph>{!data.page.supported && <Alert type="warning" message="此 Wiki 尚未提供对当前来源版本有效的正式证据。" />}</>}
        {data?.page?.supported && <Link to={`/spaces/${pathId(selection.space)}/pages/${pathId(data.page.id)}${query({ revision: data.page.revision })}`}>浏览已发布 Wiki</Link>}
        <Typography.Paragraph type="secondary">这里只确认当前发布版本可读、有效且引用所选来源；浏览原文、版本差异与审核使用正式 Wiki 页面。</Typography.Paragraph>
      </Card>
      <Card id="onboarding-5" title="6. 完成首次引用问答" extra={status(checks.answer, "等待本条知识链路的完整回答")}>
        <Space wrap><Link to="/agents">配置或选择智能体</Link><Link to="/chat">开始带引用问答</Link></Space>
        <Typography.Paragraph>在问答页选择覆盖本空间的智能体与范围。完成后返回，选择会话和回答重新核验。</Typography.Paragraph>
        <Choice label="本次问答会话" value={selection.session} choices={data?.sessions ?? []} disabled={!data} onChange={value => choose("session", value)} />
        <Choice label="本次回答" value={selection.run} choices={data?.runs ?? []} disabled={!data || !selection.session} onChange={value => choose("run", value)} />
        {(data?.sessionsMore || data?.runsMore) && <Typography.Paragraph type="secondary">此处显示当前一页可访问记录；更多会话与历史请到问答页浏览。</Typography.Paragraph>}
        {data?.run?.hidden ? <Alert type="warning" message="此回答的依赖已不可访问，不能用作完成凭据。" /> : data?.run && <><Typography.Paragraph>{checks.answer ? "已核验当前 Wiki 的引用回答" : "所选回答尚未完整完成，或未引用本次 Wiki 与来源版本。"}</Typography.Paragraph><Link to={`/runs/${pathId(data.run.id)}`}>查看所选回答</Link></>}
        <Typography.Paragraph type="secondary">只有服务端已完成、仍获授权，并在事实或推断段落中引用本次 Wiki 与当前来源的回答，才通过本步检查。这里不缓存回答正文。</Typography.Paragraph>
      </Card>
    </div>
  </section>;
}