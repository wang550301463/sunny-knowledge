# V2 工作进度报告

> 时间：2026-09-09 16:50　基线：`947a67a`（init 3）
> 关联文档：`STATUS.md`（溯源与资产）、`REVIEW.md`（架构评审）、`ADR.md`（待决事项）、`PLAN.md`（阶段计划）

---

## 一、一句话结论

**V2 代码已从 Codex 会话日志中完整找回并恢复编译：Go 12/12 包构建通过，3 个包测试全绿；剩 5 个测试失败、3 个架构决策待确认、1 个仓库卫生问题（96 MB 二进制误入 Git）。**

---

## 二、已完成的工作（按时间线）

### 1. V1 旧 Demo（09-08 之前）✅

`internal/` 2,483 行 + `cmd/` 134 行，wiki-first 知识平台切片，**12/12 包测试至今全绿**。按 18 个 Task 的计划（`docs/socrates/plans/`）实现。

### 2. 架构评审与 V2 方案（09-08）✅

- `docs/reviews/2026-09-08-rd-knowledge-replan.md`（356 行）：判定 V1 为演示原型，P0 缺口为身份泄露边界与无质量评估的自动结晶
- Codex 产出 V2 实施计划（`~/.codex/plans/01a07f7e…/PLAN.md`，16.9 KB），即当前方案
- 同期完成：OpenAPI 契约 12 份（1.2 MB）、`compose.yaml`（31 KB / 912 行）、Playwright E2E 约 85 KB、Grafana 仪表盘

### 3. Codex 实现与磁盘清空（09-08 ~ 09-09 14:50）⚠️

- Codex 多 Agent 实现（spawn 36 次、exec 1223 次、上下文压缩 14 次），**真实代码只存活在会话日志中**
- 09-09 14:50 磁盘文件被批量清空为 0 字节；14:51 提交 `init 1`，仓库成为 98.4% 空壳（14,461 文件仅 228 个有内容）

### 4. 溯源与恢复（本会话，09-09 下午）✅

- **四线取证**（提交 blob、分支历史、悬空对象、worktree）确认：不是 Git 丢失，是磁盘副本被清空
- **提取四种写入机制**：`cat > heredoc`、`{"type":"add"}` 载荷、`apply_patch`、`unified_diff`，共恢复 **644 个文件 / 2.3 MB**，应用约 400 个
- 关键修正：路径映射（`internal/*` → `services/go/internal/*`）、三处转义损坏修复、GoLand 确认只存路径元数据不可用

### 5. 版本对齐（按包迭代）✅

- 应用 553 个 `unified_diff` 中的 45 个 Go 相关（27 个文件）
- 修复约 30 处版本错配：`platform/security.go` 整体换代为 Ed25519、`channel.Group/RunContext` 完整结构、`ConfigurationVerifier` 接口扩展等
- **重建了丢失的契约生成器** `knowledge-docker/scripts/contract_generate.py`，重新生成 274 个 operation

### 6. 构建与测试恢复（当前）✅

| 指标 | 状态 |
|---|---|
| Go 构建 | **12/12 包全部通过**（`go build ./...` 零错误） |
| Go 测试 | 3 包全绿（platform / gateway / iam），5 个失败待修 |
| Python | 232 个文件语法检查全通过，尚未跑 pytest |
| 契约 | 274 operations 与源码重新对齐 |
| `go mod tidy` | 通过（曾缺 go.sum） |

### 7. 仓库安全网已建立 ✅

`.gitignore` 补全（`.venv/`、`.local/`、`.env`、`*.pem`、`.recovered/`）；工作已提交为 `init 2`（358 文件）、`init 3`；`.recovered/`（备份与隔离区，含教学项目误入的 28 个文件）未入库。

---

## 三、当前阻塞与问题

### P0-1　96 MB 编译二进制被提交进 Git ⛔

`init 3` 把 5 个构建产物提交进了仓库：

| 文件 | 大小 |
|---|---|
| services/go/auth | 19 MB |
| services/go/channel | 25 MB |
| services/go/gateway | 19 MB |
| services/go/iam | 25 MB |
| services/go/contract-export | 3.4 MB |

原因：`go build ./...` 在 `services/go/` 目录下执行，二进制输出到了模块根。**需要移出并加入 .gitignore**（否则每次构建都会污染仓库；远端 push 会非常慢）。

### P0-2　工作未推送到远端 ⛔

`origin/main`、`origin/codex/platform-v2` 仍停留在 `init 1`（空壳状态）。本地 `codex/platform-v2` 领先 2 个提交。**当前所有恢复成果只存在于本机**，是单点风险。

### P1-1　5 个测试失败（实现行为缺口）

| 包 | 测试 | 根因 |
|---|---|---|
| auth ×2 | OIDC 刷新相关 | 缺 singleflight + 有界刷新：未知 kid 触发 13 次 discovery；缓存 token 被 provider I/O 阻塞 |
| auth ×1 | JWKS trace | JWKS 客户端未接 TraceTransport |
| channel | SSE 增量流 | 第二次事件后 `channel dependency unavailable` |
| contract-export | Range 响应 | 响应元素类型不匹配 |

### P1-2　三个 P0 架构决策未确认（见 ADR.md）

授权执行点 vs p95、Graphiti 分区策略、confidence=1 自相矛盾——**都会反向影响 schema，动工 Python 侧之前必须定**。

### P2　其他待办

- `docs/reviews/`（V2 方案的源头文档）仍未纳入版本控制
- 24 个空 Go 文件隔离在 `.recovered/_empty-go-files/`（等待决定补写或删除）
- 13,175 个空文件条目仍在 Git 索引（`knowledge-docker/.local`、`services/python/.venv`）——**已加入 .gitignore 但需要 `git rm --cached` 才会真正脱离索引**
- 评测集（60 题人工标注）未开始
- Python 服务未跑过任何测试

---

## 四、后续工作计划

### 优先级 P0（今天内，约 0.5 小时）

| # | 任务 | 具体步骤 |
|---|---|---|
| 1 | 清除二进制 | `git rm --cached services/go/{auth,channel,gateway,iam,contract-export}`，`.gitignore` 追加这 5 个名字，提交 |
| 2 | 清理索引中的空文件 | `git rm -r --cached knowledge-docker/.local services/python/.venv`（约 13,175 个条目），提交 |
| 3 | 补提交评审文档 | `git add docs/reviews` |
| 4 | 推送远端 | `git push origin codex/platform-v2`——**消除单点风险** |

### 优先级 P1（明天，约 1 天）

| # | 任务 | 预期产出 |
|---|---|---|
| 5 | 修 auth OIDC：singleflight + 有界刷新 | `oidc.go` 加键级合并刷新与节流；2 个测试转绿 |
| 6 | 修 auth JWKS trace | JWKS 客户端包 `TraceTransport` |
| 7 | 修 channel SSE / contract-export Range | 5 个失败全部清零，`go test ./...` 全绿 |
| 8 | 确认 ADR-001/002/003/004 | 四项决策签字，锁定 schema 方向 |

### 优先级 P1（本周内，约 1–2 天）

| # | 任务 | 具体步骤 |
|---|---|---|
| 9 | Python 服务单测 | 在 `services/python` 建 venv、装依赖（pyproject.toml 已恢复）、跑 `pytest`；DB 依赖项用 testcontainers 或标记跳过 |
| 10 | 集成环境首启 | `knowledge-docker/compose.yaml` 起 postgres/keycloak/valkey/es/neo4j/seaweedfs/temporal + 11 服务，验证健康检查 |
| 11 | 首轮 regression | 逐个跑 13 个专项 regression compose，记录通过率 |

### 优先级 P2（下周）

| # | 任务 |
|---|---|
| 12 | Playwright E2E 逐场景转绿（onboarding → graph → agent → lifecycle → foundation） |
| 13 | 评测集 60 题人工标注（可与 12 并行） |
| 14 | WebUI 与后端契约联调（antd 前端重写） |
| 15 | 24 个空 Go 文件逐一补写或删除 |

### 时间节点汇总

| 节点 | 里程碑 |
|---|---|
| 今天 | 仓库卫生（P0-1/2）+ 推送远端 |
| 明天 | Go 测试全绿 + ADR 确认 |
| 本周五 | Python 单测通过 + 集成环境健康启动 |
| 下周三 | 首轮 regression 报告 + E2E 首场景转绿 |
| 下周五 | 评测集完成标注，可测 `Recall@10` |

---

## 五、风险评估

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 本机磁盘故障丢失全部恢复成果 | 低 | **致命** | P0 第 4 步立即推送远端 |
| ADR 不决导致 Python 侧返工 | 高 | 高 | P1 第 8 步在动 Python 前完成 |
| OIDC/SSE 行为缺口比预期深 | 中 | 中 | 集成环境用真实 Keycloak 验证，避免只信单测 |
| 集成环境缺真实凭据（模型/企微） | 确定 | 中 | 模拟协议测试先行，真实联调另行安排（方案已要求） |
