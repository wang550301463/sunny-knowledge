# Web V2 剩余实施与验收

本文件记录尚未完成的 Web 工作，不缩小 `docs/v2/PLAN.md` 的完整批准范围；基础界面或协议测试通过不等于 V2 完成。

## 问答、检索与持久运行

- 对接 Agent sessions/runs 正式契约：创建会话、恢复授权后历史、发送、快速/深入分析模式、本轮空间范围、选定 immutable model/agent config。
- 真实 BM25 + vector + RRF + rerank 检索、授权证据列表、事实/推断/缺口标记、检索失败与无结果分开显示。
- 中央可观察工具阶段与回答增量，不显示隐藏思维；右侧精确引用抽屉复用现有组件。
- SSE durable event cursor 重连、乱序/重复过滤、停止、预算/部分结果、重试新 run、滚动锚点保持。
- 反馈、导出、会话清空、已撤销证据导致的历史重新授权和不可复用说明。
- 公开的普通用户可用模型能力发现：当前 `/models` 管理接口需要 platform_admin，不能在前端假定默认模型或越权读取。

## 空间与知识演化

- 来源目录、后缀/状态过滤、批量同步；当前已有实际来源列表/配置/预览/同步/删除/任务。
- 接入任务主动更新与阶段细节、解析诊断导航至原文、公开授权后的原始文件选择器。
- 结构化 EvidenceRef 与声明可视化编辑器，代替当前高级 JSON 输入；服务端完整验证仍保留。
- Wiki 授权页面搜索与目录；当前分页列表过滤仅作用于已加载页。
- 更完整的活动审计、原始快照 revision diff 与来源变更失效说明。
- 图谱 1–2 跳、类型/方向/时间筛选、证据路径支持、图故障 partial 提示；图不得被展示为正式知识来源。

## 智能体与渠道

- 四类 preset 的真实创建、编辑、内部工具范围、测试、版本、发布、分享（分享不授予数据权）。
- user ∩ agent ∩ turn ∩ tool ∩ channel ∩ audience scope 展示与验证。
- 高级 prompt/model/tool/budget 配置默认收起，显式 model config pinning。
- 企业微信 Bot 配置/测试/启用、状态诊断、单用户绑定/解绑/重绑、群受众授权、受保护引用链接。
- MCP OAuth / preregistered client 配置与使用说明、Agent 复用诊断。

## 体验与真实验收

- 用 Docker 真实 Keycloak 执行 PKCE 登录/刷新/退出/无效 state/nonce/过期会话与多并发请求测试。
- Playwright 完整 onboarding → source preview → task → Wiki → cite → edit/review → rollback → chat/agent/channel。
- 真实浏览器桌面/平板/小屏以及弹窗、抽屉、键盘焦点、reduced-motion 与无障碍检查；本次只完成本地登录页视觉检查。
- 实时权限撤销、scope 切换、旧请求延迟、服务断开后不可残留旧内容的完整跨服务测试。
- 真实供应商模型/工具/流式测试，与本地协议模拟证据分别记录。
- 静态资源路由按业务拆分与更细粒度 UI chunk 优化；当前构建的 Ant Design vendor chunk 约 800 kB（gzip 约 251 kB）。
- 生成 OpenAPI clients 的共享版本机制，当前 central typed REST types 手动对应服务契约。