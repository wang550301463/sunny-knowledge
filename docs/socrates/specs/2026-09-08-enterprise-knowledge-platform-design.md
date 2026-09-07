# 企业知识平台设计规格

日期：2026-09-08  
仓库：sunny-knowledge  
依据：[LLM Wiki v2](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2)（Karpathy LLM Wiki 的生产扩展）

## 1. 目标

做内部单租户的企业知识平台，不是检索插件。知识要能看见、能引用、能随时间自我进化。

核心纪律与 gist 对齐：**stop re-deriving, start compiling。** 编译后的 wiki 页是知识主存；图是导航与时序投影；原文 chunk 只做逐字引用。图不替换页面，也不作为第二份事实库。

成功标准（第一期切片即可验证）：

- 一次查询能同时用到：代码编译页、制度原文引用、工单时间线。
- 新工单能使旧结论在图上失效（`supersedes`），wiki 页标记过期声明，历史保留。
- 左图右页：力导向图可点选，右侧打开对应 wiki；失效边仍可见。
- 该仓库的文件作为图节点存在；至少有若干篇真实 procedural 页，不是空目录。
- 制度变更、实体合并、改 schema 必须进人工队列并写审计日志。

## 2. 范围

### 2.1 纳入

- 三类一等来源：代码/仓库、制度/PDF/文档、会话/工单。
- 第一期语料切片：一个核心仓库为主，配相关制度，再加少量工单（三条管道都接通，评测偏代码）。
- 文件级节点、procedural 页、力导向可视化（第一期就要能演示）。
- 遗忘曲线、多 Agent 同步（规格必有，第二期紧接着交）。
- 内部角色：读者 / 编辑 / 管理员。
- ingest 剥密钥；所有写操作可审计。

### 2.2 明确不做（当前产品）

- 多租户、对外 SaaS、计费。
- Zep Cloud / `zep-go`。图层只自建 Graphiti。
- RAGFlow GraphRAG（避免第二张实体图）。
- 把整本 PDF 当作 Graphiti episode。
- 用 chunk 检索结果覆盖已编译 wiki 声明。

### 2.3 交付波次

| 波次 | 内容 |
|------|------|
| 第一期 | schema、最小 wiki、RAGFlow 解析与引用、自建 Graphiti 投影与 supersession、wiki+图查询、结晶、该仓库 File 节点、procedural 实页、力导向左图右页、审核队列、审计 |
| 第二期 | 访问日志驱动的遗忘/降权、多 Agent 观察合并与页级协调、private→shared 晋升 |
| 更后 | 遗忘参数调优、跨仓库文件降噪策略、输出格式扩展（简报/导出） |

## 3. 架构

```
raw（代码快照 / 制度原文 / 工单原文）
  ├─ 制度/PDF → RAGFlow 解析 → 仍落在 raw，再编译为 wiki 条款页
  ├─ 代码 → 直接编译为 wiki（服务、模块、文件、流程）
  └─ 工单 → Graphiti episode；结晶后升为 wiki 页
        ↓
wiki 编译页 + schema（知识主存，Go 管理）
        ↓
自建 Graphiti（Neo4j）：页面与会话的类型化、时序投影。企业知识使用单一 `group_id`，不用 per-user thread 记忆模型。
        ↓
查询（Go）：wiki 页 BM25+向量 + Graphiti 混合检索/图遍历
        └─ 需要逐字证据时才拉 RAGFlow chunk
        ↓
结晶：合格回答写回 wiki，再投影到图
```

冲突时 **wiki 声明优先**，图必须跟着改。chunk 不能推翻编译页。

### 3.1 进程与语言

| 部分 | 实现 |
|------|------|
| sunny-knowledge | Go：schema、wiki 编译、查询编排、结晶、审核、审计、可视化 API |
| Graphiti | 官方 Python 服务（FastAPI）自建部署；Go 仅 HTTP 客户端 |
| 图数据库 | Neo4j 5.26+（默认）。FalkorDB 仅作备选，第一期不双后端 |
| RAGFlow | sidecar；关闭 `use_graphrag` |
| 可视化前端 | TypeScript，只消费 Go API，不存知识 |

Go 不内嵌 Graphiti、不重写时序图引擎。

建议模块：

```
cmd/api          查询 / 结晶 / 审核 / 可视化读模型
cmd/ingest       代码、制度、工单接入
cmd/lint         定时巩固、第二期遗忘
internal/wiki    页面、index、schema
internal/graph   Graphiti HTTP 客户端
internal/docs    RAGFlow HTTP 客户端（解析+引用）
internal/evolve  冲突、失效、人审
internal/cite    从 chunk 取逐字证据
internal/access  查询访问日志（第二期遗忘依赖此）
```

## 4. 本体与 schema

schema 文件（`AGENTS.md` / 等价物）是系统里最重要的文件。它规定实体与边、各类 source 的 ingest 规则、何时新建页 vs 更新页、矛盾处理、巩固节奏、private vs shared。人和 LLM 共同演进该文件。

### 4.1 节点

| 类型 | 来源 | 记忆层级 | 波次 |
|------|------|----------|------|
| Service / Module | 代码编译页 | semantic | 1 |
| File | 该仓库文件；`owned_by` 服务 | semantic | 1 |
| Person | 代码归属、工单处理人 | semantic | 1 |
| Dependency | 仓库依赖 | semantic | 1 |
| Decision | 结晶后的架构结论 | semantic | 1 |
| Policy | 制度编译页（不是 PDF chunk） | semantic | 1 |
| Incident / Change | 工单 episode，可升成页 | episodic → semantic | 1 |
| Procedure | `wiki/procedures/` | procedural | 1（必须有实页） |

第一期 File：对该核心仓库建点（路径、所属服务）。全公司文件全量建点会噪；后续仅「被 wiki/工单引用」的文件提升权重，未引用者随遗忘降权。

### 4.2 边

`depends_on` / `uses` / `owns` / `cites` / `governed_by` / `caused` / `fixed` / `supersedes` / `contradicts`

每条边带：来源（wiki 页或 episode）、置信度、有效期。新工单与旧结论冲突走 `supersedes`，旧边失效，不删历史。

Graphiti ingest 使用自定义实体/边，并尽量约束到上述类型（避免泛实体淹没企业图）。

### 4.3 巩固层级

- working：未编译的 raw / 新观察  
- episodic：工单与会话 episode  
- semantic：wiki 概念/实体/摘要页  
- procedural：重复语义提升后的标准做法页  

### 4.4 冲突规则（写进 schema）

1. 编译页声明优先于图；漂移时改图不改口。  
2. chunk 只引用，不覆盖编译声明。  
3. 制度变更、实体合并、改 schema → 人工队列。  
4. 工单导致的事实过期 → 可自动失效，必须写审计。  

## 5. 查询

编排在 Go，不写进 RAGFlow 内核。

1. 按 schema 分类：结构/影响、时序/过期、需要原文引用。  
2. 主检索：wiki 页（BM25 + 向量）+ Graphiti 混合检索；关系题从中心节点最多 2 hop。  
3. 引用：仅当需要逐字贴制度/PDF 时，沿 `cites` 取 RAGFlow chunk。  
4. 加权 RRF：wiki/图权重大于 chunk。等权融合禁止作为默认。  

简单结构题走 wiki 页，不默认走 chunk。

## 6. 结晶与进化

合格问答是 source，不是一次性 RAG 输出。

- 质量过线 → digest 页（问题、结论、实体、教训）写入 wiki。  
- 新事实投影到 Graphiti；冲突则 `supersedes`。  
- 钩子（第一期）：on new source 自动编译；on query 结晶门控。  
- 钩子（第二期）：on schedule 遗忘衰减；多 Agent 写入时矛盾检测。  

全自动改写正式知识不允许。可自动：抽取、标冲突、过期失效、补坏链、降权。必须人审：改本体、合并实体、覆盖已发布结论。

## 7. 可视化

wiki 的输出面，不是第三份知识库。

- 布局：左图右页。  
- 左：力导向图，按类型着色；`supersedes` 失效边用不同样式保留；可按来源过滤（代码 / 制度 / 工单）。第一期就要是可给企业看的主视图，不是调试器。  
- 右：wiki 页；展开制度原文片段；工单时间线（成立 / 失效）。  
- 点 File 节点打开所属服务页并高亮该文件。  
- 管理员：审核队列。读者无写接口。  
- 第二期：时间滑条叠加遗忘降权（低置信度节点变淡）。  

Go 提供读模型：邻域图、wiki 页、引用、待审项。前端不持久化知识。

## 8. 第二期：遗忘与多 Agent

### 8.1 遗忘曲线

查询 API 记录访问（`internal/access`）。定时任务按类型衰减未强化声明的置信度：架构决策/制度慢，瞬时 bug 与未被引用的文件快。只降权、不删除。强化（再次访问、新来源确认）重置曲线。Graphiti 边失效表达「被取代」；遗忘表达「很久没人提起」。

### 8.2 多 Agent 同步

共享同一 wiki。默认 last-write-wins + 时间戳；内容冲突进人工覆盖。private 观察可晋升为 shared。轻量协调：哪一页正在被哪次会话编译，避免重复劳动。不是独立任务系统。

## 9. 失败处理

- Graphiti 写入失败：wiki 已存在则保留，图标「投影滞后」，可重试。  
- RAGFlow 解析失败：raw 留下，不编假条款页。  
- 检索超时：先返回 wiki 命中，图降级，答案标明图未就绪。  
- ingest 前剥离密钥、token、密码。  

## 10. 验证

评测集偏代码，三类题都要有（结构、制度引用、工单是否改写旧结论）。指标：

- 过期事实错误率（工单之后是否仍把旧结论当现行）。  
- 引用是否落到编译页或原文，而不是无出处生成。  
- 结晶是否写回 wiki。  
- File 节点能否从服务走到具体文件。  
- procedural 页能否回答「我们怎么做变更/发版」。  
- 力导向图能否选出节点并打开右侧页面。  

第二期加：长期未访问声明是否被降权；两个并发 ingest 是否不丢页、冲突是否入队。

不把 Recall@k 当作唯一指标。
