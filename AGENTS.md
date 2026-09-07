# sunny-knowledge schema

内部单租户企业知识平台。wiki 是知识主存；Graphiti 是导航与时序投影；RAGFlow chunk 只做逐字引用。

## 实体

Service, Module, File, Person, Dependency, Decision, Policy, Incident, Change, Procedure

## 边

depends_on, uses, owns, cites, governed_by, caused, fixed, supersedes, contradicts

企业知识使用单一 `group_id=enterprise`。

## Ingest

- 代码：编译为 wiki 页（含 File 列表与 procedures），再投影为 Graphiti episode。禁止把整本 PDF 当 episode。
- 制度：解析后写条款页；解析失败只保留 raw，不编假页。关闭 RAGFlow GraphRAG。
- 工单：episode + 必要时 supersedes；wiki 声明标 stale。

## 冲突

1. 编译页优先于图。
2. chunk 不能覆盖编译声明。
3. 改 schema、合并实体、覆盖正式结论 → 人审。
4. 工单导致的过期可自动失效，必须审计。

## 结晶

问答质量 ≥ 0.7 写 `summaries/digest-latest.md`，并 `last_reinforced=now`、confidence=1。

## 遗忘

访问刷新 `last_accessed`。定时 `go run ./cmd/lint` 按半衰期衰减 confidence，只降权不删。

| 类型 | 半衰期（天） |
|------|----------------|
| Policy / Decision / Procedure | 180 |
| Service / Module / Person / Dependency | 90 |
| File / Incident / Change | 21 |

## 多 Agent

页锁默认 5 分钟。无锁时后写胜（LWW），正文冲突入 `write_conflict` 人审。private 页必须在 `private/{agentID}/`，可用 promote 升为 `entities/` 下 shared。
