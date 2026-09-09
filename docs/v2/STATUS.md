# V2 基线实测与溯源（STATUS）

> 生成时间：2026-09-09　基线：`a3533d5`（`codex/platform-v2`）
> 全部数字来自本机实测，命令可直接复现。**不要用文件数量或目录结构判断进度。**

---

## 0. 溯源结论：代码没丢在 Git 里，而是写在 Codex 会话中、磁盘文件随后被清空

用户疑问：「可能丢了一部分代码，也可能单纯重构一半」。

**Git 线取证**：V2 实现从未进入任何提交——`init 1` 里那些实现文件的 blob 本来就是 0 字节，不存在「写好再删」的第二次提交，悬空对象也无业务代码。

**Codex 线取证（补充，见 §0.1）**：实现**确实写过**。Codex 用 `cat > <路径> <<'EOF'` 写入了真实代码，命令入参完整保存在 `~/.codex/sessions/**/*.jsonl` 中；而磁盘上的同名文件在 2026-09-09 14:50（`init 1` 提交前一分钟）被批量清空为 0 字节。

**最终结论**：不是「没写」，也不是「Git 里丢了」，而是**实现内容只存活在 Codex 会话日志里，磁盘副本被清空**。已从日志恢复 180 个文件 / 679.2 KB 真实代码。

### 证据链

| # | 取证点 | 结果 |
|---|---|---|
| 1 | `a3533d5` 提交内实现文件的 blob 大小 | `git cat-file -p a3533d5:services/python/src/knowledge_platform/knowledge/app.py \| wc -c` → **0**；`retrieval/service.py`、`ingest/compiler.py`、`agent/service.py` 同样为 0。即这些文件**入库时就是空的**，不是后来被删 |
| 2 | V2 分支提交数 | `codex/platform-v2` 从 `7bf0f36`（71 文件）拉出后**只有 1 个提交**「init 1」。**不存在「先写好再删除」所需的第二次提交** |
| 3 | 悬空对象 | `git fsck --lost-found` 仅 2 个：blob `6f2fc22`（本次改动前的 fixture 旧版）、tree `d693d26`（init 1 之前的旧树）。`git count-objects -v` 显示 pack 仅 **2 KB**。无任何丢失的业务代码 |
| 4 | worktree | `.worktrees/platform-v2`（分支 main，`a3533d5`）`git status` 只有 `?? .idea/`，**无未提交代码**。它比主检出多的 8.1 MB 全部是 `web/node_modules`（@ant-design/icons、jsdom），不是源码 |
| 5 | 真实内容的分布形态 | 真实内容全部集中在**上游资产**（设计文档、OpenAPI 契约、Docker 编排、Playwright E2E、Grafana），而**下游实现**（Python 7 个服务）为 0 行。符合「先写规约与验收、后写实现」的执行顺序在**实现阶段开始前被中断** |

### 时间线还原

| 时间 | 事件 | 产物 |
|---|---|---|
| 09-08 前 | V1「wiki-first 知识平台切片」按 18 个 Task 实现 | `internal/` 2,483 行 + `cmd/` 134 行，**12/12 包测试绿**；计划见 `docs/socrates/plans/2026-09-08-phase1-knowledge-platform.md`（73 KB / 18 Task） |
| 09-08 | 架构评审，指出 P0：身份与知识泄露边界、无质量评估的自动结晶 | `docs/reviews/2026-09-08-rd-knowledge-replan.md`（356 行，**至今未提交**，`git status` 中为 `?? docs/reviews/`） |
| 09-09 13:51 | 建 worktree `.worktrees/platform-v2`、分支 `codex/platform-v2` | — |
| 09-08～09-09 | Codex 多 Agent 实现（spawn_agent 36 次、exec 1223 次、上下文压缩 14 次） | 真实代码，仅存于 `~/.codex/sessions/**/*.jsonl` |
| 09-09 14:50 | **磁盘文件被批量清空为 0 字节**（空文件 mtime 集中于此） | — |
| 09-09 14:51 | 提交「init 1」，一次性加入 14,390 个文件，98.4% 为空 | 契约、编排、E2E 有内容；实现代码全空 |
| 09-09 15:0x | 本次核查：修复 2 项（§5）、从 Codex 日志恢复 180 个文件（§0.1） | — |

---

### 0.1 从 Codex 会话日志恢复的代码

来源：`~/.codex/sessions/**/*.jsonl`（58 个 rollout），主会话 `01a07f7e-a961-7253-b55f-d9f438d55678`
（在 Codex 中名为「规划企业级知识库架构」，rollout 53 MB，工作区即 sunny-knowledge，workdir 常为 `.worktrees/platform-v2`）。

已从 `cat > <路径> <<'EOF' … EOF` 命令入参中还原，**共 180 个 sunny-knowledge 文件、679.2 KB**，
落盘于 `.recovered/codex-20260908/`：

| 分类 | 数量 | 说明 |
|---|---|---|
| 磁盘为空，可直接回填 | 109 | 零风险 |
| 磁盘缺失，属新增 | 51 | 需按路径映射放置 |
| 磁盘已有内容，需人工判断 | 20 | **勿直接覆盖** |

内容质量抽验（确为生产级代码，非桩）：

- `services/python/src/knowledge_platform/ingest/analyzers/manifests.py`（18 KB）—— Maven/Gradle 构建清单与锁文件适配器
- `services/python/src/knowledge_platform/graphiti/backend.py`（8.5 KB）—— 真实 Graphiti 0.30.1 SDK 集成
- `internal/channel/store.go`（10 KB）—— 企微渠道配置持久化，含 `channel_configs` 表与 `SecretBox` 加密

**路径偏移需特别注意**：`internal/{channel,iam,auth,gateway,platform}/` 下的 Go 文件，
其 import 指向 `services/go/internal/`，回填时应映射到 `services/go/internal/` 而非仓库根 `internal/`
（后者是 V1 旧 Demo 的 46 个包，不能混）。

GoLand 侧：`~/Library/Caches/JetBrains/GoLand2026.2/LocalHistory/changes.storageData`（11.8 MB）
只记录了**路径元数据**，6 类代码内容标记（`from knowledge_platform`、`async def` 等）命中数均为 0，
**不含文件正文**，无法用于内容恢复。

详见 `.recovered/codex-20260908/_README.md` 与 `_MANIFEST.csv`。

---

## 1. 整体度量

| 指标 | 实测值 |
|---|---|
| `git ls-files` 文件总数 | 14,461 |
| 非零字节文件数 | **228（1.6%）** |
| 零字节文件数 | **14,233（98.4%）** |
| 被跟踪文件总大小 | **1.8 MB** |

```bash
git ls-files -z | xargs -0 stat -f "%z" | awk '{s+=$1; if($1==0)e++; else n++} END {printf "总 %.1f MB, 非空 %d, 空 %d\n", s/1048576, n, e}'
```

真实文件的顶层分布（228 个）：

| 目录 | 真实文件数 | 说明 |
|---|---|---|
| `knowledge-docker/` | 94 | 编排 + E2E + 可观测性（**含 51 个 `.local/` 副本**） |
| `internal/` | 46 | V1 旧 Demo，可用 |
| `services/` | 42 | Go 32 个 + `go.sum` + `pyproject.toml` + `.venv` 残留 |
| `web/` | 15 | V1 前端 + `package-lock.json` |
| `contracts/` | 14 | **12 份 OpenAPI + manifest + coverage** |
| `testdata/` | 7 | 切片语料 |
| `cmd/` | 3 | V1 入口 |
| `docs/` | 2 | socrates 计划与规格（`docs/reviews/` 未提交） |

---

## 2. 可用资产清单（应作为 V2 起点）

### 2.1 OpenAPI 契约 —— 最有价值 ✅

| 项 | 值 |
|---|---|
| `contracts/generated/openapi/` | 12 份，合计约 1.2 MB |
| operation 数 | gateway **100**、agent 38、knowledge 139 KB、ingest 70 KB、llm 79 KB、iam 85 KB、channel 66 KB、auth 52 KB、retrieval 40 KB、mcp 25 KB、graphiti 17 KB |
| 生成物 | TS `client.ts` / `types.ts` / `operations.ts`、`manifest.json`（sha256 锁定）、`coverage.json` |

契约先行的基础已经具备，**不要重写**。

### 2.2 Docker 编排 ✅

`knowledge-docker/compose.yaml`（**31 KB / 912 行**）已含：
postgres、keycloak、valkey、elasticsearch、neo4j、seaweedfs、temporal + 11 个服务 + 6 个 worker + regression 容器。
另有 **13 个专项 regression compose**（agent / channel / gateway / graph / mcp / retrieval / snapshot / web-* / performance / postgres-restore）。

### 2.3 Playwright E2E 验收套件 ✅（约 85 KB 真实测试）

| 场景 | 文件 | 大小 |
|---|---|---|
| 首次使用引导 | `tests/web_onboarding/onboarding.spec.js` + `fixtures.js` | 15.4 + 6.4 KB |
| 图谱 | `tests/web_graph/graph.spec.js` + `fixtures.js` + `fault-gate.js` | 13.1 + 7.6 + 2.7 KB |
| 智能体 / 渠道 | `tests/web_agent/agent.spec.js` + `channel-result.spec.js` + `fixtures.js` | 12.2 + 5.4 + 8.2 KB |
| 生命周期（版本/审核/回滚） | `tests/web_lifecycle/lifecycle.spec.js` | 8.2 KB |
| 基础 | `tests/web/foundation.spec.js` + `fixtures.js` | 5.3 + 3.0 KB |

这些测试定义了行为契约，是驱动实现的现成验收标准。**实现阶段应让它们逐个转绿。**

### 2.4 可观测性 ✅

`observability/`：Grafana `knowledge.json`（10.5 KB 仪表盘）+ datasources + dashboards、prometheus.yaml、tempo.yaml、collector.yaml；`compose.observability.yaml` 3.6 KB。

### 2.5 评测语料 ⚠️

`evaluation/corpus.json` 已固定公开仓库与 commit（spring-petclinic、zod 等），但状态字段自述为 `preparing_unreviewed_cases`——**尚无人工标注**。方案要求的 60 题标注集仍需从零建立。

---

## 3. 不可用 / 需重建的部分

### 3.1 V2 Go 服务 —— 薄且不可构建 ❌

| 项 | 值 |
|---|---|
| `services/go` Go 文件 | 84 个：**52 个为空**，32 个非空，合计 1,929 行 |
| `go build ./...` | **失败** |
| 原因 1 | 空文件 → `internal/auth/channel_read.go:1:1: expected 'package', found 'EOF'` |
| 原因 2 | `go.sum` 不完整 → `missing go.sum entry for github.com/golang-jwt/jwt/v5`、`github.com/jackc/pgx/v5` |
| 原因 3 | `go.mod` 要求 `go >= 1.23.0`；本机 `/usr/local/go` 为 **1.22.0**（直接拒绝构建），`/opt/homebrew/bin/go` 为 **1.26.1** |
| 已有真实模块 | `iam`（accounts / audiences / policy）、`auth`（oidc / security）、`channel`（protocol / clients / diagnostics）、`gateway`（main / limiter）、`platform`（http / security / client） |

> 本次已执行 `go mod download`（`go.sum` 现为 4,261 B，标记为 `M`）。**空文件补齐后必须重跑 `go mod tidy`**，否则仍缺条目。

### 3.2 V2 Python 服务 —— 全空 ❌

| 项 | 值 |
|---|---|
| `services/python/src/**/*.py` | **118 个，全部 0 字节** |
| `services/python/tests/**/*.py` | **113 个测试文件，全部 0 字节** |
| `services/python/.venv/**` | 10,666 个文件被跟踪，其中仅有极少数真实文件（如 `temporalio`、`urllib3` 的个别文件），**不是可用虚拟环境** |

覆盖 7 个服务目录：`knowledge` / `ingest` / `retrieval` / `llm` / `graphiti` / `agent` / `mcp`。目录结构完整，零实现。

### 3.3 伪验收产物 —— 应删除 ⚠️

| 项 | 值 |
|---|---|
| `knowledge-docker/.local/**` | 2,509 个被跟踪文件，多数为空；含 `acceptance-*` / `reviewed-build-*` / `platform-recovery-*` 等目录与 517 个 `.log` |
| 其中 `.env` | 7 个 |
| 其中 `*.pem` | 63 个（路径如 `identities/auth/private.pem`） |

**澄清**：这些 `.env` 与 `*.pem` 的 blob 哈希均为 `e69de29bb2d1d6434b8b29ae775ad8c2e48c5391`（Git 空文件哈希），即**全部 0 字节，当前没有真实密钥泄漏**。
但路径名会诱导后续真凭据静默入库。

```bash
git ls-files -s | grep -E "\.pem$|\.env" | head
# 100644 e69de29bb2d1d6434b8b29ae775ad8c2e48c5391 0	.../identities/auth/private.pem
```

---

## 4. V1 旧 Demo（真实可用，是领域模型参考）

| 项 | 值 |
|---|---|
| `internal/` | 46 个 Go 文件，2,483 行 |
| `cmd/` | 3 个入口，134 行 |
| `go build ./cmd/... ./internal/...` | 通过 |
| `go test ./internal/...` | **12/12 包通过** |

按方案「旧 Demo 保留在 Git 历史中」，V2 推进后应从工作树移除 `internal/`、`cmd/`、`web/`（V1 前端），避免与 V2 的 Go 模块、依赖和 CI 互相干扰。

---

## 5. 本次已修复项

| # | 问题 | 处理 | 验证 |
|---|---|---|---|
| F3 | 根模块 `go build ./...` / `go test ./...` 被测试语料打断：`services/python/tests/ingest/fixtures/monorepo/worker/service.go:5:2` 引入了不存在的 `example.com/storage/client` | 给该语料加 `//go:build ignore`（它只是 ingest 解析器的输入数据，不是可编译代码） | 根模块 `go build ./...` 退出码 0；`go test ./internal/...` **12 包全绿** |
| F4 | `.gitignore` 缺 `.venv/`、`.local/`、`.env`、`*.pem` 规则——这是 1.3 万个空文件入库的直接原因 | 已补全，保留原有 7 行规则 | — |

---

## 6. 待执行清理（需确认，会改动 Git 索引）

```bash
# 1) 从索引移除伪产物与假 venv（不删工作区文件）
git rm -r --cached --quiet knowledge-docker/.local services/python/.venv
git commit -m "chore: drop empty placeholder artifacts from index"

# 2) 物理删除
rm -rf knowledge-docker/.local services/python/.venv

# 3) 提交未纳入版本控制的评审文档
git add docs/reviews && git commit -m "docs: add 2026-09-08 architecture review"
```

> 第 1 步会改动约 13,175 个索引条目，请确认后执行。

---

## 7. 「完成」的判据（建议写入 CI）

一个服务或能力只有同时满足以下条件才算完成，**目录存在不算**：

1. 源文件非空，且通过语言级构建（Go `go build`；Python `ruff` + `pytest`）；
2. 有针对真实依赖的测试（非全流程 mock），且通过；
3. 契约生成物与源码一致（`contracts/generated/manifest.json` 的 sha256 校验通过）；
4. 在 `knowledge-docker/compose.yaml` 中健康启动，并通过对应 regression compose；
5. 对应 Playwright 场景转绿。

**门禁建议**：

```bash
find . -path ./web/node_modules -prune -o -type f -empty -print | grep . && exit 1
```
