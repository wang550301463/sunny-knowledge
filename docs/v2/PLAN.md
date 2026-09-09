# V2 落地计划（PLAN）

> 基线：`a3533d5`　日期：2026-09-09
> 配套文档：`STATUS.md`（现状与溯源）、`REVIEW.md`（架构评审）、`ADR.md`（决策记录）

## 0. 这份计划的前提

1. **V2 实现从未开始**，不是代码丢失——无需回滚或找回（证据见 `STATUS.md` §0）。
2. 已具备的真实资产应直接使用，不重写：OpenAPI 契约 1.2 MB、`compose.yaml` 31 KB、Playwright E2E 约 85 KB、Grafana 仪表盘。
3. 「完成」的判据不是目录存在，而是 `STATUS.md` §7 的 5 条（构建 / 测试 / 契约一致 / 编排启动 / E2E 转绿）。
4. 阶段 0 与 ADR-001/002/003/004 的确认**必须先于**阶段 2 的 schema 落地，否则返工。

---

## 阶段 0：基线修复（0.5 天，可立即执行）

| # | 任务 | 产出 / 判据 |
|---|---|---|
| 0.1 | 从索引移除伪产物（需确认） | `git rm -r --cached knowledge-docker/.local services/python/.venv` |
| 0.2 | 补齐 `.gitignore` | ✅ 已完成（`.venv/` `.local/` `.env` `*.pem`） |
| 0.3 | 修复根模块构建阻断 | ✅ 已完成（fixture 加 `//go:build ignore`）；`go build ./...` 退出码 0，12 包测试绿 |
| 0.4 | 统一 Go 工具链 | `services/go/go.mod` 要求 ≥1.23.0，本机 `/usr/local/go` 为 1.22.0。容器与本地统一到 1.26.x，并在 CI 固定 |
| 0.5 | 提交遗漏的评审文档 | `git add docs/reviews`（当前为未跟踪状态） |
| 0.6 | 加空文件门禁 | CI 增加 `find . -type f -empty` 检查，防止骨架式提交复发 |

---

## 阶段 1：基础平台

**目标**：四个 Go 服务可构建、可启动；IAM/Auth 授权闭环；WebUI 骨架。

| # | 任务 | 说明 |
|---|---|---|
| 1.1 | 填充 `services/go` 空的 52 个文件 | 现有 32 个非空文件已是骨架，优先补齐 `gateway`（现仅 37 行）、`auth`、`iam`、`channel` |
| 1.2 | `go mod tidy` + `go build ./...` 通过 | 空文件补齐后才可能成功 |
| 1.3 | IAM：用户 / 部门 / 用户组 / 服务账号 / 空间 / 授权关系 | 沿用 `services/go/internal/iam/{accounts,audiences,policy,policy_batch}.go` |
| 1.4 | Auth：Keycloak OIDC 校验 + scope + 基于 IAM 的授权决策 | 按 **ADR-001**：auth 只签发 ACL 版本，不参与查询热路径 |
| 1.5 | Gateway：路由 / 限流 / 追踪 / SSE / MCP 转发 / 清理外部伪造身份头 | 现有 `limiter.go`、`platform/http.go` 可复用 |
| 1.6 | WebUI 框架：React + TS + Vite + Ant Design，5 项主导航 | 现有 `web/` 是 V1 的 JSX 实现，建议新建；可复用 `web/src/styles.css` 与 `package-lock.json` 中的 antd 依赖线索 |
| 1.7 | 契约回归：12 份 OpenAPI 与实现一致 | `manifest.json` sha256 校验；`.github/workflows/contracts.yml` 已有 |

**验收**：`docker compose -f knowledge-docker/compose.yaml up` 中 gateway/iam/auth/channel 健康；Keycloak 登录后可拿到带 scope 的 token；`tests/web/foundation.spec.js` 转绿。

---

## 阶段 2：知识闭环

**目标**：真实来源 → 三语言解析 → wiki 修订 → 审核发布 → 证据 → 投影，全链路跑通。

| # | 任务 | 说明 |
|---|---|---|
| 2.1 | PostgreSQL schema：wiki 正文 / 结构化声明 / 不可变修订 / 审核 / outbox | 发布必须比较 `base_revision`，正文+版本+审计+outbox 同事务 |
| 2.2 | 稳定 ID：来源命名空间 | 防同名文件/模块被合并 |
| 2.3 | Connector → LanguageAnalyzer → KnowledgeCompiler → ProjectionAdapter | 扩展接口统一 |
| 2.4 | Tree-sitter + 构建清单 / 锁文件适配器（Java / TS / Go） | 不执行仓库构建或安装脚本；无法静态解析标记未知 |
| 2.5 | 不可变快照 → SeaweedFS S3 | — |
| 2.6 | 编译 → 校验/审核 → 发布 → 投影 → 对账 | 按 ADR-003 走 Temporal；幂等靠 `UNIQUE(source_id, source_version, compiler_version)` |
| 2.7 | 熔断（REVIEW P1-4） | 单次变更 > 10% 转人工审核；按语言/仓库分批；一键回滚生成新修订 |
| 2.8 | Python 服务骨架填充：`knowledge` / `ingest` | 7 个服务目录已存在，逐个填 |

**验收**：`tests/web_lifecycle/lifecycle.spec.js`（版本差异 / 审核 / 回滚）转绿；重复投递、乱序事件、并发修订、删除、工单失效场景有测试。

---

## 阶段 3：检索问答

**目标**：真实模型适配 + ES 混合检索 + 权限过滤 + 引用。

| # | 任务 | 说明 |
|---|---|---|
| 3.1 | `llm` 服务：Chat / Embedding / Reranker 适配器 + 并发/超时/重试/用量 | — |
| 3.2 | `retrieval`：ES 索引投影 + BM25 50 / 向量 50 + 等权 RRF(k=60) 取 30 | 参数作为可配置、可评测的默认值 |
| 3.3 | 权限过滤（ADR-001） | ES `terms(acl_subjects, acl_version)`，不查 auth |
| 3.4 | 分层指标（REVIEW P1-1） | 召回后 / 过滤后 / 重排后分别统计 |
| 3.5 | 标注集（REVIEW P2-2） | 60 题，标注权限依赖与时态依赖；`evaluation/corpus.json` 现有仓库清单可用 |
| 3.6 | 最多 12 条主要证据 + 关系证据补齐；区分事实/推断/缺口 | — |

**验收**：`Recall@10 ≥ 0.85`；10 万片段 / 10 路并发本地检索 `p95 ≤ 2s`（外部模型耗时单列）；引用支持度与时态正确性专项检查。

---

## 阶段 4：图与时序

**目标**：Graphiti 真实适配 + 邻接遍历 + 历史查询 + 依赖与故障分析。

| # | 任务 | 说明 |
|---|---|---|
| 4.1 | 分区策略落地（**ADR-002**） | `group_id = 空间`；受限文档不进图，只留占位节点 + 证据指针 |
| 4.2 | 真实邻接遍历：默认 1 跳、最多 2 跳；限关系类型/方向/时间；≤100 节点 / ≤200 边 | 需自定义 Cypher（Graphiti SDK 不做 ACL 过滤） |
| 4.3 | 图候选回映射证据，权重 0.5 参与融合；去重后 ≤60 条进重排 | — |
| 4.4 | 双时间：业务有效时间 + 系统获知时间 | 区分仓库提交状态与生产部署状态；无部署证据不推断生产版本 |
| 4.5 | 推断关系单独标识 | 不自动成为正式结论 |
| 4.6 | 权限收紧 → 立即阻断旧投影 + 异步重建 | — |
| 4.7 | 图不可用降级 | 返回并说明缺失能力 |

**验收**：`tests/web_graph/graph.spec.js` + `fault-gate.js` 转绿；依赖路径与历史故障类问题在标注集上达标。

---

## 阶段 5：Agent 与入口

**目标**：自定义 Agent、运行恢复、MCP、企微绑定与群权限。

| # | 任务 | 说明 |
|---|---|---|
| 5.1 | 4 类预设 Agent：知识问答 / 依赖影响分析 / 故障追溯 / 知识维护 | — |
| 5.2 | 运行范围交集：`用户 ∩ Agent ∩ 本轮 ∩ 工具`（企微再叠加渠道与群受众） | 按 REVIEW P1-3：**交集在检索请求里一次性下推** |
| 5.3 | 预算：8 轮决策 / 20 次工具调用 / 2 个并行只读 / 180s | 超限返回已有结果 + 未完成部分 |
| 5.4 | 配置按运行快照冻结；资源授权实时检查 | — |
| 5.5 | 运行事件 SSE 续传（带游标）；重试创建新运行 | — |
| 5.6 | MCP：Streamable HTTP + OAuth，预注册客户端 | `search` / `get` / `traverse` / `timeline` / `ask` / `feedback`；反馈需独立写 scope |
| 5.7 | 企微：WebSocket 长连接、BotID/BotSecret、消息去重、单机器人租约、退避重连 | [官方 SDK](https://github.com/WecomTeam/aibot-node-sdk) |
| 5.8 | 身份绑定：双向一次性挑战（短期有效、单次使用、可解绑重绑） | — |
| 5.9 | 群聊：仅响应 @；管理员登记群并配置可公开范围 | 明确该授权面向群内当前及后续成员 |

**验收**：`tests/web_agent/agent.spec.js` + `channel-result.spec.js` 转绿；范围交集 / 预算 / 取消 / 重试 / 事件续传有测试；协议模拟覆盖重连、重复消息、流结束、超长 UTF-8、账号重绑、群隔离。

---

## 阶段 6：交付验证

| # | 任务 |
|---|---|
| 6.1 | Docker 集成测试（13 个 regression compose 逐个通过） |
| 6.2 | 权限专项：跨空间、收紧文档、部门变更、撤权、服务账号、图摘要、历史会话、导出、群聊泄露 |
| 6.3 | 来源与版本专项：三语言真实仓库、重复投递、乱序、并发修订、删除、工单失效、审核冲突、回滚 |
| 6.4 | 检索评测：60 题，`Recall@10 ≥ 0.85` + 引用支持度 + 时态正确性 |
| 6.5 | 性能与恢复：10 万片段 / 10 路并发 `p95 ≤ 2s`；服务重启、模型超时、ES/图故障、投影积压；PG/S3 恢复后重建 ES 与回放图投影 |
| 6.6 | 真实模型与真实企微机器人联调（**模拟不能替代**） |
| 6.7 | 运维文档：初始化向导、MCP 接入示例、企微配置说明、评测报告、备份恢复手册 |

---

## 并行建议

可并行推进、互不阻塞的两条线：

- **线 A（后端）**：阶段 1 → 2 → 3（依赖链紧，串行）
- **线 B（验收资产）**：阶段 3.5 标注集 + 阶段 6.2 权限用例矩阵 —— 这两项**不依赖实现完成**，越早开始越好，且是阶段 3/6 的前置

---

## 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| ADR-001/002/003 未及时确认 | 契约与 schema 返工 | 阶段 2 开工前必须冻结 |
| 空壳文件被误当作进度 | 进度失真 | 阶段 0.6 空文件门禁 + `STATUS.md` §7 判据 |
| Go 工具链分裂（1.22 vs 1.26） | 本地能跑、CI 不能跑 | 阶段 0.4 统一 |
| 标注集滞后 | `Recall@10` 无法测量，调优失焦 | 线 B 提前启动 |
| 12+ 有状态组件的运维面 | 交付与运维成本 | ADR-003 限制 Temporal 范围；其余按 compose 固化 |
