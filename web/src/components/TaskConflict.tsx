import { useState } from "react";
import { Alert, Button, Collapse, Descriptions, Form, Input, Typography } from "antd";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { ApiError, isAuthorizationFailure, pathId } from "../api";
import { useMutation, useResource } from "../hooks";
import type { EvidenceRef, Proposal, TaskConflictArtifact, WikiPage } from "../types";
import { ErrorNotice, ResourceView } from "./Common";
import { ContentDiff, SafeMarkdown } from "./Content";
import { CitationDrawer, EvidenceList } from "./Citation";

export function TaskConflict({ taskId, step }: { taskId: string; step: number }) {
  const { api } = useAuth();
  const endpoint = `/tasks/${pathId(taskId)}/conflicts/${step}`;
  const resource = useResource(`conflict:${taskId}:${step}`, async (signal) => {
    const artifact = await api.get<TaskConflictArtifact>(endpoint, signal);
    const current = artifact.current_revision
      ? await api.get<WikiPage>(`/pages/${pathId(artifact.page_id)}`, signal)
      : null;
    if (current && current.current_revision !== artifact.current_revision)
      throw new ApiError(409, "version_conflict");
    return { artifact, current };
  });
  return (
    <>
      <Button onClick={resource.refresh}>重新读取并比较</Button>
      <ResourceView resource={resource}>
        {({ artifact, current }) => (
          <ConflictContent artifact={artifact} current={current} endpoint={endpoint} />
        )}
      </ResourceView>
    </>
  );
}

function ConflictContent({ artifact, current, endpoint }: {
  artifact: TaskConflictArtifact;
  current: WikiPage | null;
  endpoint: string;
}) {
  const { api } = useAuth();
  const mutation = useMutation();
  const [proposalId, setProposalId] = useState(artifact.proposal_id);
  const [citation, setCitation] = useState<EvidenceRef | null>(null);
  const content = artifact.request.content;
  const refs = content ? [...content.evidence, ...content.claims.flatMap((claim) => claim.evidence)] : [];
  // Stop future display after a live denial or stale comparison. Previously
  // authorized bytes already delivered to the browser cannot be recalled.
  if (isAuthorizationFailure(mutation.error) ||
      (mutation.error instanceof ApiError && mutation.error.status === 409))
    return <ErrorNotice error={mutation.error} />;
  return (
    <>
      <Alert type="warning" message="接入结果发生版本冲突，尚未自动发布" description="下方保留最初冻结的请求。比较当前版本后，可将同一份冻结正文提交为知识审核提案。" />
      <Descriptions column={1} items={[
        { key: "page", label: "目标页面", children: artifact.page_id },
        { key: "source", label: "来源配置版本", children: artifact.source_version },
        { key: "operation", label: "冻结操作", children: artifact.operation },
        { key: "original", label: "原始请求基准", children: artifact.original_base_revision ?? "无（首次创建）" },
        { key: "review", label: "首次审核基准", children: artifact.review_base_revision ?? "无" },
        { key: "current", label: "本次比较的当前版本", children: artifact.current_revision ?? "无已发布版本" },
      ]} />
      {content ? (
        <>
          <Typography.Title level={4}>冻结提议正文</Typography.Title>
          <Typography.Title level={5}>{content.title}</Typography.Title>
          <SafeMarkdown text={content.markdown} />
          <EvidenceList evidence={refs} onSelect={setCitation} />
          <Typography.Title level={4}>与当前完整内容的差异</Typography.Title>
          <ContentDiff before={JSON.stringify(current?.revision?.content ?? null, null, 2)} after={JSON.stringify(content, null, 2)} />
        </>
      ) : (
        <Alert type="info" message="此结构化冲突没有页面正文" description="请根据冻结请求中的证明与来源证据处理；此处不会生成或提交替代正文。" />
      )}
      <Typography.Title level={4}>原始冻结请求</Typography.Title>
      <pre className="audit-detail">{JSON.stringify(artifact.request, null, 2)}</pre>
      {artifact.comparison && <Collapse items={[{
        key: "comparison", label: "首次冲突时记录的内容校验值",
        children: <pre className="audit-detail">{JSON.stringify(artifact.comparison, null, 2)}</pre>,
      }]} />}
      {proposalId ? (
        <Alert type="success" message={`已有审核提案：${proposalId}`} description={<Link to="/reviews">前往知识审核</Link>} />
      ) : content && (
        <Form layout="vertical" onFinish={(values: { reason: string }) => void mutation.run(
          () => api.post<Proposal>(`${endpoint}/propose`, { base_revision: artifact.current_revision, reason: values.reason }),
          (proposal) => setProposalId(proposal.id),
        )}>
          <Form.Item name="reason" label="冲突提案理由" rules={[{ required: true, whitespace: true, message: "请说明比较结果和提案理由" }]}>
            <Input.TextArea rows={3} maxLength={4096} />
          </Form.Item>
          <ErrorNotice error={mutation.error} />
          <Button type="primary" htmlType="submit" loading={mutation.busy}>基于已比较版本提交提案</Button>
        </Form>
      )}
      <CitationDrawer evidence={citation} onClose={() => setCitation(null)} />
    </>
  );
}
