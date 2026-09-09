


import { useState } from 'react';
import { Alert, Button, Collapse, Descriptions, Drawer, Form, Input, List, Modal, Popconfirm, Select, Space, Switch, Tag, Typography } from 'antd';
import { CloudSyncOutlined, EyeOutlined, FileOutlined, PlusOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { useAuth } from '../auth';
import { ApiError, pathId, query } from '../api';
import { useMutation, useResource } from '../hooks';
import type { IngestTask, ListResult, SourcePreview, SourceRecord } from '../types';
import { ErrorNotice, formatDate, RefreshButton, ResourceView } from '../components/Common';

const taskLabels: Record<string, string> = {
  queued: '排队中', running: '处理中', succeeded: '已完成',
  review_needed: '需要审核', superseded: '已被更新版本替代', failed: '失败',
};

const INTERVAL_PRESETS = [
  { value: 600, label: '10 分钟' },
  { value: 1800, label: '30 分钟' },
  { value: 3600, label: '1 小时' },
  { value: 14400, label: '4 小时' },
  { value: 86400, label: '每天' },
];

export function SourcesPanel({ spaceId, onManageACL }: { spaceId: string; onManageACL?: () => void }) {
  const { api } = useAuth();
  const [cursor, setCursor] = useState<string>();
  const [editing, setEditing] = useState<SourceRecord | 'new'>();
  const [selected, setSelected] = useState<SourceRecord>();
  const [filter, setFilter] = useState('');
  const sources = useResource(`sources:${spaceId}:${cursor}`, (signal) =>
    api.get<ListResult<SourceRecord>>(`/sources${query({ space_id: spaceId, cursor, limit: 50 })}`, signal),
  );
  return (
    <>
      <div className="section-toolbar">
        <Input.Search placeholder="筛选本页数据来源" aria-label="筛选来源"
          value={filter} onChange={(e) => setFilter(e.target.value)} />
        <Space>
          <RefreshButton onClick={() => { setSelected(undefined); sources.refresh(); }} />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing('new')}>
            添加来源
          </Button>
        </Space>
      </div>
      <ResourceView resource={sources}>
        {(data) => (
          <>
            <List className="panel"
              dataSource={data.items.filter((s) => s.name.toLowerCase().includes(filter.toLowerCase()))}
              locale={{ emptyText: '暂无数据来源。添加来源后，先预览原始快照，再启动同步。' }}
              renderItem={(source) => (
                <List.Item
                  actions={[
                    <Button key="detail" onClick={() => setSelected(source)}>预览与同步</Button>,
                    <Button key="edit" disabled={source.state === 'deleted'} onClick={() => setEditing(source)}>更新配置</Button>,
                  ]}
                >
                  <List.Item.Meta
                    avatar={<FileOutlined className="list-icon" />}
                    title={
                      <Space>
                        {source.name}
                        <Tag>{source.kind}</Tag>
                        <Tag>v{source.version}</Tag>
                        {source.state === 'deleted' && <Tag>已删除</Tag>}
                        {source.auto_sync && (
                          <Tag icon={<CloudSyncOutlined />} color="processing">
                            自动同步 · 每 {Math.round((source.auto_sync_interval_seconds ?? 3600) / 60)} 分钟
                          </Tag>
                        )}
                      </Space>
                    }
                    description={
                      <>
                        <div>{String(source.config.url ?? source.config.path ?? source.config.endpoint ?? '')}</div>
                        <span>
                          {source.preview
                            ? `${source.preview.file_count} 个文件 · 已固定原始版本`
                            : '尚未预览'}
                          {' · '}
                          {formatDate(source.updated_at)}
                        </span>
                      </>
                    }
                  />
                </List.Item>
              )}
            />
            <div className="pagination">
              <Button disabled={!cursor} onClick={() => setCursor(undefined)}>第一页</Button>
              <Button disabled={!data.next_cursor} onClick={() => setCursor(data.next_cursor ?? undefined)}>下一页</Button>
            </div>
          </>
        )}
      </ResourceView>
      {editing !== undefined && (
        <SourceForm spaceId={spaceId} source={editing === 'new' ? undefined : editing}
          onClose={() => setEditing(undefined)} onSaved={() => { setEditing(undefined); sources.refresh(); }} />
      )}
      <Drawer width={900} title={selected?.name ?? '数据来源'}
        open={!!selected} onClose={() => setSelected(undefined)} destroyOnHidden>
        {selected && (
          <SourceActions key={`${selected.id}:${selected.version}`}
            source={selected} onChanged={sources.refresh} onManageACL={onManageACL} />
        )}
      </Drawer>
    </>
  );
}

export function SourceForm({ spaceId, source, onClose, onSaved }: {
  spaceId: string;
  source?: SourceRecord;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { api } = useAuth();
  const [form] = Form.useForm<SourceFields>();
  const kind = Form.useWatch('kind', form) ?? source?.kind ?? 'git';
  const mutation = useMutation();
  const clearSecrets = () => {
    for (const key of ['username', 'password', 'ssh_private_key', 'ssh_known_hosts']) {
      (form.setFieldValue as (k: string, v: unknown) => void)(key, '');
    }
  };
  const submit = (values: SourceFields) => {
    const credential = Object.fromEntries(
      (['username', 'password', 'ssh_private_key', 'ssh_known_hosts'] as const)
        .flatMap((k) => values[k] ? [[k, values[k] as string]] : []),
    );
    clearSecrets();
    const config = values.kind === 'git'
      ? { url: values.url, ref: values.ref }
      : values.kind === 'oss'
        ? { endpoint: values.endpoint, bucket: values.bucket, prefix: values.prefix ?? '' }
        : { path: values.path, content: values.content };
    const secretField = values.clearCredential
      ? { credential: null }
      : Object.keys(credential).length ? { credential } : {};
    void mutation.run(
      () => source
        ? api.patch(`/sources/${pathId(source.id)}`, {
            base_version: source.version, name: values.name, config, ...secretField,
            auto_sync: (values.kind === 'git' || values.kind === 'oss') && values.auto_sync === true,
            auto_sync_interval_seconds: values.auto_sync_interval_seconds ?? 3600,
          })
        : api.post('/sources', {
            name: values.name, space_id: spaceId, kind: values.kind, config, ...secretField,
            auto_sync: (values.kind === 'git' || values.kind === 'oss') && values.auto_sync === true,
            auto_sync_interval_seconds: values.auto_sync_interval_seconds ?? 3600,
          }),
      () => onSaved(),
    );
  };
  return (
    <Modal open width={820} title={source ? '更新来源配置' : '添加数据来源'}
      footer={null} onCancel={() => { clearSecrets(); onClose(); }} destroyOnHidden>
      <Form form={form} layout="vertical" onFinish={submit}
        initialValues={{
          name: source?.name ?? '', kind: source?.kind ?? 'git',
          url: source?.config.url ?? '', ref: source?.config.ref ?? 'refs/heads/main',
          endpoint: source?.config.endpoint ?? '', bucket: source?.config.bucket ?? '',
          prefix: source?.config.prefix ?? '', path: source?.config.path ?? '',
          content: '', clearCredential: false,
          auto_sync: source?.auto_sync === true,
          auto_sync_interval_seconds: source?.auto_sync_interval_seconds ?? 3600,
        }}>
        <div className="form-grid">
          <Form.Item label="来源名称" name="name"
            rules={[{ required: true, whitespace: true, message: '请输入来源名称' }]}>
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item label="来源类型" name="kind">
            <Select disabled={!!source} options={[
              { value: 'git', label: 'Git 仓库（GitLab / GitHub 等）' },
              { value: 'oss', label: '对象存储（OSS / S3）' },
              { value: 'markdown', label: 'Markdown 文档' },
              { value: 'ticket', label: 'JSON 工单' },
            ]} />
          </Form.Item>
        </div>
        {kind === 'git' && (
          <>
            <Form.Item name="url" label="仓库 URL"
              rules={[{ required: true, message: '请输入 HTTPS 或 SSH 仓库 URL' }]}>
              <Input placeholder="https://git.example/team/repo.git" autoComplete="off" />
            </Form.Item>
            <Form.Item name="ref" label="分支 / 标签 / 提交引用"
              rules={[{ required: true, message: '请指定引用' }]}>
              <Input placeholder="refs/heads/main" />
            </Form.Item>
            <Collapse items={[{
              key: 'credential', forceRender: true,
              label: source?.has_credential ? '访问凭据（已配置，留空保留）' : '私有仓库访问凭据',
              children: <>
                <Typography.Paragraph type="secondary">
                  凭据仅写入服务端，提交后输入清空。SSH 必须同时提供用户名、私钥与已验证的 known_hosts。
                </Typography.Paragraph>
                <div className="form-grid">
                  <Form.Item name="username" label="用户名"><Input autoComplete="off" /></Form.Item>
                  <Form.Item name="password" label="访问令牌 / 密码">
                    <Input.Password visibilityToggle={false} autoComplete="new-password" />
                  </Form.Item>
                </div>
                <Form.Item name="ssh_private_key" label="SSH 私钥">
                  <Input.Password visibilityToggle={false} autoComplete="new-password" />
                </Form.Item>
                <Form.Item name="ssh_known_hosts" label="SSH known_hosts">
                  <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} autoComplete="off" />
                </Form.Item>
                {source?.has_credential && (
                  <Form.Item name="clearCredential" label="清除已有访问凭据" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                )}
              </>,
            }]} />
          </>
        )}
        {kind === 'oss' && (
          <>
            <Form.Item name="endpoint" label="Endpoint（HTTPS）"
              rules={[{ required: true, message: '请输入对象存储 Endpoint' }]}>
              <Input placeholder="https://oss-cn-hangzhou.aliyuncs.com" autoComplete="off" />
            </Form.Item>
            <div className="form-grid">
              <Form.Item name="bucket" label="Bucket" rules={[{ required: true, message: '请输入 Bucket' }]}>
                <Input autoComplete="off" />
              </Form.Item>
              <Form.Item name="prefix" label="对象前缀（可选）">
                <Input placeholder="docs/" autoComplete="off" />
              </Form.Item>
            </div>
            <Collapse items={[{
              key: 'credential', forceRender: true,
              label: source?.has_credential ? '访问凭据（已配置，留空保留）' : 'AccessKey 凭据',
              children: <>
                <Typography.Paragraph type="secondary">
                  用户名填 AccessKeyId，访问令牌填 AccessKeySecret（阿里云 OSS、腾讯 COS、AWS S3、MinIO 等 S3 兼容存储均可）。
                </Typography.Paragraph>
                <div className="form-grid">
                  <Form.Item name="username" label="AccessKeyId"><Input autoComplete="off" /></Form.Item>
                  <Form.Item name="password" label="AccessKeySecret">
                    <Input.Password visibilityToggle={false} autoComplete="new-password" />
                  </Form.Item>
                </div>
                {source?.has_credential && (
                  <Form.Item name="clearCredential" label="清除已有访问凭据" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                )}
              </>,
            }]} />
          </>
        )}
        {(kind === 'markdown' || kind === 'ticket') && (
          <>
            <Form.Item label="相对文件路径" name="path"
              rules={[{ required: true, message: '请输入相对路径' }]}>
              <Input placeholder={kind === 'markdown' ? 'docs/guide.md' : 'tickets/INC-1.json'} />
            </Form.Item>
            {source && (
              <Alert type="info" message="已保存的上传正文不通过配置接口返回。更新时请提供该版本完整原文。" />
            )}
            <Form.Item name="content" label={kind === 'markdown' ? 'Markdown 原文' : 'JSON 工单原文'}
              rules={[{ required: true, message: '请提供完整原文' }]}>
              <Input.TextArea className="editor-textarea" autoSize={{ minRows: 10, maxRows: 20 }} />
            </Form.Item>
            <Typography.Paragraph type="secondary">
              原始文件最大 4,000,000 字节。JSON 工单按提供的原文保留，不自动重写内容。
            </Typography.Paragraph>
          </>
        )}
        {(kind === 'git' || kind === 'oss') && (
          <div className="form-grid">
            <Form.Item name="auto_sync" label="自动同步" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="auto_sync_interval_seconds" label="同步间隔（秒）" initialValue={3600}>
              <Select options={INTERVAL_PRESETS} />
            </Form.Item>
          </div>
        )}
        {(kind === 'git' || kind === 'oss') && (
          <Alert type="info" showIcon
            message="开启自动同步后，远端出现新版本（Git 新提交 / 对象变更）将自动进入审核流；相同内容不会重复同步。全部历史版本与证据链保留可追溯。" />
        )}
        <ErrorNotice error={mutation.error} />
        <div className="form-footer">
          <Typography.Text type="secondary">保存配置后需先预览，再发起同步。</Typography.Text>
          <Space>
            <Button onClick={() => { clearSecrets(); onClose(); }}>取消</Button>
            <Button type="primary" htmlType="submit" loading={mutation.busy}>
              {source ? '保存新版本' : '添加来源'}
            </Button>
          </Space>
        </div>
      </Form>
    </Modal>
  );
}

export function SourceActions({ source, onChanged, onManageACL }: {
  source: SourceRecord;
  onChanged: () => void;
  onManageACL?: () => void;
}) {
  const { api } = useAuth();
  const [preview, setPreview] = useState<SourcePreview>();
  const [task, setTask] = useState<IngestTask>();
  const [taskId, setTaskId] = useState<string>();
  const mutation = useMutation();
  const deleted = source.state === 'deleted';
  const triggerAuto = async () => {
    try {
      const outcome = await api.post<{ changed: boolean; version?: number }>(`/sources/${pathId(source.id)}/auto-sync`, {});
      if (outcome.changed) { onChanged(); }
    } catch (e) { /* surfaced via mutation.error */ }
  };
  return <>
    <Descriptions column={1} size="small" items={[
      { key: 'kind', label: '来源类型', children: source.kind },
      { key: 'version', label: '配置版本', children: `v${source.version}` },
      { key: 'resource', label: '权限资源', children: source.resource_id },
      { key: 'credential', label: '访问凭据', children: source.has_credential ? '已配置（不可读取）' : '未配置' },
      { key: 'preview', label: '固定版本', children: preview?.source_revision ?? source.preview?.source_revision ?? '尚未预览' },
      source.auto_sync && { key: 'autosync', label: '自动同步', children: <Tag icon={<CloudSyncOutlined />} color="processing">已开启</Tag> },
    ].filter(Boolean) as { key: string; label: string; children: React.ReactNode }[]} />
    <div className="section-toolbar">
      <Space wrap>
        <Button icon={<EyeOutlined />} disabled={deleted} loading={mutation.busy}
          onClick={() => void mutation.run(() => api.post<SourcePreview>(`/sources/${pathId(source.id)}/preview`, { base_version: source.version }), (result) => { setPreview(result); onChanged(); })}>
          预览原始快照
        </Button>
        <Button type="primary" icon={<CloudSyncOutlined />}
          disabled={deleted || (!preview && !source.preview)} loading={mutation.busy}
          onClick={() => void mutation.run(() => api.post<IngestTask>(`/sources/${pathId(source.id)}/sync`, { base_version: source.version }), (result) => { setTask(result); onChanged(); })}>
          启动同步
        </Button>
        {source.auto_sync && (
          <Button loading={mutation.busy} onClick={() => void triggerAuto()}>
            立即自动同步
          </Button>
        )}
        <Popconfirm title="删除此数据来源？" description="保留原始快照与审计，并创建移除审核提案；已排队的旧任务会被阻止发布。"
          onConfirm={() => mutation.run(() => api.delete<IngestTask>(`/sources/${pathId(source.id)}`, { base_version: source.version }), (result) => { setTask(result); onChanged(); })}>
          <Button danger disabled={deleted}>删除来源</Button>
        </Popconfirm>
      </Space>
    </div>
    <ErrorNotice error={mutation.error} />
    {mutation.error instanceof ApiError && mutation.error.code === 'worker_authorization_required' && (
      <Alert type="warning" showIcon
        message="接入 worker 尚未获得知识权限"
        description={<span>
          空间负责人需为已登记的 worker 服务账号配置空间及来源资源的读取、编辑授权。
          {onManageACL
            ? <Button type="link" onClick={onManageACL}>打开空间授权</Button>
            : <Link to={`/spaces/${pathId(source.space_id)}?tab=settings`}>打开空间授权</Link>}
        </span>} />
    )}
    <Alert type="info" message="预览只固定原始快照，不发布知识。同步会返回持久任务，完成状态由 worker 实际执行结果决定。" />
    {task && (
      <div className="task-notice">
        <Tag color="blue">{taskLabels[task.status]}</Tag>
        <span>已受理任务 {task.id} </span>
        <Button size="small" onClick={() => setTaskId(task.id)}>查看任务</Button>
      </div>
    )}
    {taskId && <TaskDetailInline id={taskId} />}
  </>;
}

function TaskDetailInline({ id }: { id: string }) {
  const { api } = useAuth();
  const task = useResource(`task:${id}`, (signal) => api.get<IngestTask>(`/tasks/${pathId(id)}`, signal));
  return <ResourceView resource={task}>{(value) => (
    <Descriptions column={1} size="small" items={[
      { key: 'stage', label: '阶段', children: value.stage },
      { key: 'updated', label: '更新', children: formatDate(value.updated_at) },
      value.error_code && { key: 'err', label: '错误', children: <Tag color="red">{value.error_code}</Tag> },
    ].filter(Boolean) as { key: string; label: string; children: React.ReactNode }[]} />
  )}</ResourceView>;
}

interface SourceFields {
  name: string;
  kind: SourceRecord['kind'];
  url?: string;
  ref?: string;
  endpoint?: string;
  bucket?: string;
  prefix?: string;
  path?: string;
  content?: string;
  username?: string;
  password?: string;
  ssh_private_key?: string;
  ssh_known_hosts?: string;
  clearCredential?: boolean;
  auto_sync?: boolean;
  auto_sync_interval_seconds?: number;
}
