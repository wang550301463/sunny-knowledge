# sunny-knowledge

内部企业知识平台：wiki 主存 + 自建 Graphiti 投影 + 制度引用。Go 控制面。

## 测试

```
go test ./...
node --test web/src/graphMap.test.js
```

## 切片语料接入

```
go run ./cmd/ingest --repo testdata/slice/repo --policy testdata/slice/policies/refund.md --ticket testdata/slice/tickets/INC-1.json
SUNNY_TOKEN=secret go run ./cmd/api
# 无 Graphiti 时 ingest 会写 data/graph.json，API 启动时加载。GRAPHITI_URL=http://localhost:8000 则走自建 Graphiti。
```

遗忘任务：`go run ./cmd/lint`

两个 agent 并发写同一页会进入审核队列。可视化：`cd web && npm install && npm run dev`（代理 `/v1` 到 `:8080`）。

## 验收

- 问「我们怎么做变更」命中 procedural 页
- 问「升级 redis」答案含 redis-7，不把 redis-6 当现行
- 图上能看到失效边
- 能点到 `internal/refund/refund.go`
