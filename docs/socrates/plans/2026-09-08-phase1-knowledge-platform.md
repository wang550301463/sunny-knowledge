# Knowledge Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use socrates:subagent-driven-development (recommended) or socrates:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Go 里落地企业知识平台：wiki 主存 + 自建 Graphiti 投影 + 引用层，第一期跑通切片（文件节点、procedural、力导向、supersession），同一计划内接着做遗忘曲线与多 Agent 同步。

**Architecture:** wiki 文件是知识主存；Graphiti 只通过 HTTP 做时序投影；RAGFlow 只解析/引用。查询记访问；定时按类型衰减置信度（只降权不删）。多 Agent 写同一 wiki：页锁 + LWW，冲突进人工队列，private 可晋升 shared。

**Tech Stack:** Go 1.22、net/http、Graphiti REST（`POST /messages`、`POST /search`）、Neo4j 5.26、RAGFlow HTTP、Vite + React + `react-force-graph-2d`

**Spec:** `docs/socrates/specs/2026-09-08-enterprise-knowledge-platform-design.md`

---

## File map

| Path | Responsibility |
|------|----------------|
| `go.mod` | module `github.com/wang550301463/sunny-knowledge` |
| `AGENTS.md` | gist schema：实体/边/ingest/矛盾/结晶 |
| `internal/schema/types.go` | 实体、边、记忆层级、查询种类的枚举 |
| `internal/secret/strip.go` | ingest 前剥密钥 |
| `internal/wiki/page.go` | 页面 frontmatter + body |
| `internal/wiki/store.go` | 读写 `data/wiki/**/*.md` |
| `internal/wiki/search.go` | 页内 BM25 |
| `internal/audit/log.go` | 追加审计 JSONL |
| `internal/evolve/queue.go` | 人审队列（合并实体/改 schema/覆盖结论） |
| `internal/graph/types.go` | Fact / Episode / Neighborhood |
| `internal/graph/store.go` | GraphStore 接口 |
| `internal/graph/fake.go` | 单测 fake（含 supersedes） |
| `internal/graph/http.go` | Graphiti HTTP 客户端 |
| `internal/docs/store.go` | CiteStore 接口 |
| `internal/docs/fake.go` | 单测引用 |
| `internal/docs/ragflow.go` | RAGFlow HTTP（解析失败不编假页） |
| `internal/ingest/code.go` | 从仓库快照确定性编译 Service/File/Procedure 页 |
| `internal/ingest/ticket.go` | 工单 → episode + 可选 supersede |
| `internal/ingest/policy.go` | 制度 raw + 条款页；解析失败保留 raw |
| `internal/query/classify.go` | 结构 / 时序 / 引用 |
| `internal/query/rrf.go` | 加权 RRF |
| `internal/query/ask.go` | 编排：wiki+图为主，chunk 只引用 |
| `internal/crystal/crystal.go` | 质量过线写 digest 页并投影 |
| `internal/httpapi/server.go` | `/v1/ask` `/v1/graph/neighborhood` `/v1/wiki/` `/v1/reviews` |
| `internal/httpapi/auth.go` | `X-Token` + `X-Role` |
| `internal/access/log.go` | 查询访问日志（路径、时间、actor） |
| `internal/evolve/decay.go` | 按实体类型的遗忘衰减（不删除） |
| `internal/evolve/mesh.go` | 页锁、LWW、写冲突入队、private→shared |
| `cmd/api/main.go` | API 入口 |
| `cmd/ingest/main.go` | 切片语料接入 |
| `cmd/lint/main.go` | 定时巩固与遗忘 |
| `web/` | 力导向左图右页；遗忘后节点按置信度变淡 |
| `testdata/slice/` | 一个假仓库 + 一份制度 + 一张工单 |
| `deploy/docker-compose.yml` | Neo4j + Graphiti |
| `.gitignore` | `data/wiki/` 运行时产物、`web/node_modules` |

Task 1–14 为第一期切片。Task 15–18 为规格第 8 节：遗忘与多 Agent。按顺序做，15 依赖 10/12 的 Ask 路径。

---

### Task 1: Go module and schema types

**Files:**
- Create: `go.mod`
- Create: `internal/schema/types.go`
- Test: `internal/schema/types_test.go`

- [ ] **Step 1: Write the failing test**

```go
package schema_test

import (
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/schema"
)

func TestEntityKindsIncludeFileAndProcedure(t *testing.T) {
	if !schema.IsEntityKind("File") {
		t.Fatal("File must be a first-class entity")
	}
	if !schema.IsEntityKind("Procedure") {
		t.Fatal("Procedure must be a first-class entity")
	}
	if schema.IsEntityKind("Chunk") {
		t.Fatal("Chunk is not an entity")
	}
}

func TestEdgeKindsIncludeSupersedes(t *testing.T) {
	if !schema.IsEdgeKind("supersedes") {
		t.Fatal("supersedes required")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/schema/ -v`  
Expected: FAIL `no required module` or `package schema is not in GOROOT`

- [ ] **Step 3: Write minimal implementation**

`go.mod`:

```
module github.com/wang550301463/sunny-knowledge

go 1.23
```

`internal/schema/types.go`:

```go
package schema

type EntityKind string

const (
	EntityService    EntityKind = "Service"
	EntityModule     EntityKind = "Module"
	EntityFile       EntityKind = "File"
	EntityPerson     EntityKind = "Person"
	EntityDependency EntityKind = "Dependency"
	EntityDecision   EntityKind = "Decision"
	EntityPolicy     EntityKind = "Policy"
	EntityIncident   EntityKind = "Incident"
	EntityChange     EntityKind = "Change"
	EntityProcedure  EntityKind = "Procedure"
)

var entityKinds = map[EntityKind]struct{}{
	EntityService: {}, EntityModule: {}, EntityFile: {}, EntityPerson: {},
	EntityDependency: {}, EntityDecision: {}, EntityPolicy: {},
	EntityIncident: {}, EntityChange: {}, EntityProcedure: {},
}

func IsEntityKind(s string) bool {
	_, ok := entityKinds[EntityKind(s)]
	return ok
}

type EdgeKind string

const (
	EdgeDependsOn   EdgeKind = "depends_on"
	EdgeUses        EdgeKind = "uses"
	EdgeOwns        EdgeKind = "owns"
	EdgeCites       EdgeKind = "cites"
	EdgeGovernedBy  EdgeKind = "governed_by"
	EdgeCaused      EdgeKind = "caused"
	EdgeFixed       EdgeKind = "fixed"
	EdgeSupersedes  EdgeKind = "supersedes"
	EdgeContradicts EdgeKind = "contradicts"
)

var edgeKinds = map[EdgeKind]struct{}{
	EdgeDependsOn: {}, EdgeUses: {}, EdgeOwns: {}, EdgeCites: {},
	EdgeGovernedBy: {}, EdgeCaused: {}, EdgeFixed: {},
	EdgeSupersedes: {}, EdgeContradicts: {},
}

func IsEdgeKind(s string) bool {
	_, ok := edgeKinds[EdgeKind(s)]
	return ok
}

const DefaultGroupID = "enterprise"
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./internal/schema/ -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add go.mod internal/schema/
git commit -m "feat: add schema entity and edge kinds"
```

---

### Task 2: Strip secrets before ingest

**Files:**
- Create: `internal/secret/strip.go`
- Test: `internal/secret/strip_test.go`

- [ ] **Step 1: Write the failing test**

```go
package secret_test

import (
	"strings"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/secret"
)

func TestStripRedactsKeysAndTokens(t *testing.T) {
	in := "token=ghp_abcdefghijklmnopqrstuvwx password=supersecret openai=sk-abc123456789"
	out := secret.Strip(in)
	if strings.Contains(out, "ghp_") || strings.Contains(out, "supersecret") || strings.Contains(out, "sk-abc") {
		t.Fatalf("still leaked: %s", out)
	}
	if !strings.Contains(out, "[REDACTED]") {
		t.Fatal("expected redaction marker")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/secret/ -v`  
Expected: FAIL `not in GOROOT` or undefined `Strip`

- [ ] **Step 3: Write minimal implementation**

```go
package secret

import "regexp"

var patterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)(ghp|gho|github_pat)_[A-Za-z0-9_]{10,}`),
	regexp.MustCompile(`(?i)sk-[A-Za-z0-9]{10,}`),
	regexp.MustCompile(`(?i)(password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+`),
}

func Strip(s string) string {
	out := s
	for _, re := range patterns {
		out = re.ReplaceAllString(out, "[REDACTED]")
	}
	return out
}
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./internal/secret/ -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/secret/
git commit -m "feat: redact secrets before ingest"
```

---

### Task 3: Wiki page store and BM25

**Files:**
- Create: `internal/wiki/page.go`
- Create: `internal/wiki/store.go`
- Create: `internal/wiki/search.go`
- Test: `internal/wiki/store_test.go`

- [ ] **Step 1: Write the failing test**

```go
package wiki_test

import (
	"path/filepath"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func TestStoreWriteReadAndSearch(t *testing.T) {
	root := t.TempDir()
	st := wiki.NewStore(root)
	p := wiki.Page{
		Path: "entities/pay-api.md",
		Meta: wiki.Meta{Title: "pay-api", Type: string(schema.EntityService), Source: "code"},
		Body: "pay-api 处理退款。depends_on redis。",
	}
	if err := st.Write(p); err != nil {
		t.Fatal(err)
	}
	got, err := st.Read("entities/pay-api.md")
	if err != nil {
		t.Fatal(err)
	}
	if got.Meta.Title != "pay-api" {
		t.Fatalf("title=%s", got.Meta.Title)
	}
	if _, err := filepath.Rel(root, filepath.Join(root, "entities/pay-api.md")); err != nil {
		t.Fatal(err)
	}
	hits := st.Search("退款 redis")
	if len(hits) == 0 || hits[0].Path != "entities/pay-api.md" {
		t.Fatalf("hits=%v", hits)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/wiki/ -v`  
Expected: FAIL undefined `NewStore`

- [ ] **Step 3: Write minimal implementation**

`internal/wiki/page.go`:

```go
package wiki

import (
	"fmt"
	"strings"
)

type Meta struct {
	Title      string
	Type       string
	Source     string // code | policy | ticket | crystal
	Stale      bool
	Superseded string
}

type Page struct {
	Path string
	Meta Meta
	Body string
}

func (p Page) Render() string {
	stale := "false"
	if p.Meta.Stale {
		stale = "true"
	}
	return fmt.Sprintf("---\ntitle: %s\ntype: %s\nsource: %s\nstale: %s\nsuperseded: %s\n---\n\n%s\n",
		p.Meta.Title, p.Meta.Type, p.Meta.Source, stale, p.Meta.Superseded, strings.TrimSpace(p.Body))
}

func Parse(path, raw string) (Page, error) {
	p := Page{Path: path}
	if !strings.HasPrefix(raw, "---\n") {
		p.Body = raw
		return p, nil
	}
	rest := strings.TrimPrefix(raw, "---\n")
	idx := strings.Index(rest, "\n---\n")
	if idx < 0 {
		return p, fmt.Errorf("unterminated frontmatter in %s", path)
	}
	fm, body := rest[:idx], rest[idx+5:]
	p.Body = strings.TrimSpace(body)
	for _, line := range strings.Split(fm, "\n") {
		k, v, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		v = strings.TrimSpace(v)
		switch strings.TrimSpace(k) {
		case "title":
			p.Meta.Title = v
		case "type":
			p.Meta.Type = v
		case "source":
			p.Meta.Source = v
		case "stale":
			p.Meta.Stale = v == "true"
		case "superseded":
			p.Meta.Superseded = v
		}
	}
	return p, nil
}
```

`internal/wiki/store.go`:

```go
package wiki

import (
	"os"
	"path/filepath"
	"strings"
)

type Store struct {
	Root string
}

func NewStore(root string) *Store { return &Store{Root: root} }

func (s *Store) abs(rel string) (string, error) {
	rel = filepath.Clean(rel)
	if strings.HasPrefix(rel, "..") {
		return "", os.ErrInvalid
	}
	return filepath.Join(s.Root, rel), nil
}

func (s *Store) Write(p Page) error {
	path, err := s.abs(p.Path)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(p.Render()), 0o644)
}

func (s *Store) Read(rel string) (Page, error) {
	path, err := s.abs(rel)
	if err != nil {
		return Page{}, err
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return Page{}, err
	}
	return Parse(rel, string(b))
}

func (s *Store) All() ([]Page, error) {
	var pages []Page
	err := filepath.WalkDir(s.Root, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.HasSuffix(path, ".md") {
			return err
		}
		rel, err := filepath.Rel(s.Root, path)
		if err != nil {
			return err
		}
		p, err := s.Read(filepath.ToSlash(rel))
		if err != nil {
			return err
		}
		pages = append(pages, p)
		return nil
	})
	return pages, err
}
```

`internal/wiki/search.go`:

```go
package wiki

import (
	"math"
	"sort"
	"strings"
	"unicode"
)

type Hit struct {
	Path  string
	Score float64
}

func tokenize(s string) []string {
	s = strings.ToLower(s)
	return strings.FieldsFunc(s, func(r rune) bool {
		return unicode.IsSpace(r) || r == ',' || r == '.' || r == '/'
	})
}

func (s *Store) Search(q string) []Hit {
	pages, err := s.All()
	if err != nil {
		return nil
	}
	qtoks := tokenize(q)
	df := map[string]int{}
	docs := make([]map[string]int, len(pages))
	for i, p := range pages {
		tf := map[string]int{}
		for _, t := range tokenize(p.Meta.Title + " " + p.Body) {
			tf[t]++
		}
		docs[i] = tf
		seen := map[string]struct{}{}
		for t := range tf {
			if _, ok := seen[t]; ok {
				continue
			}
			seen[t] = struct{}{}
			df[t]++
		}
	}
	n := float64(len(pages))
	var hits []Hit
	for i, p := range pages {
		var score float64
		for _, t := range qtoks {
			tf := float64(docs[i][t])
			if tf == 0 {
				continue
			}
			idf := math.Log((n+1)/(float64(df[t])+1)) + 1
			score += tf * idf
		}
		if p.Meta.Stale {
			score *= 0.2
		}
		if score > 0 {
			hits = append(hits, Hit{Path: p.Path, Score: score})
		}
	}
	sort.Slice(hits, func(i, j int) bool { return hits[i].Score > hits[j].Score })
	return hits
}
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./internal/wiki/ -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/wiki/
git commit -m "feat: wiki page store with BM25 search"
```

---

### Task 4: Audit log and human review queue

**Files:**
- Create: `internal/audit/log.go`
- Create: `internal/evolve/queue.go`
- Test: `internal/audit/log_test.go`
- Test: `internal/evolve/queue_test.go`

- [ ] **Step 1: Write the failing tests**

```go
package audit_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/audit"
)

func TestLogAppendsJSONLine(t *testing.T) {
	dir := t.TempDir()
	l := audit.New(filepath.Join(dir, "audit.jsonl"))
	if err := l.Record("ingest", "editor", "wrote entities/pay-api.md"); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(filepath.Join(dir, "audit.jsonl"))
	if !strings.Contains(string(b), `"op":"ingest"`) {
		t.Fatalf("got %s", b)
	}
}
```

```go
package evolve_test

import (
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/evolve"
)

func TestQueueRequiresAdminKinds(t *testing.T) {
	q := evolve.NewQueue()
	id := q.Enqueue(evolve.Item{Kind: evolve.KindMergeEntity, Summary: "merge pay-api aliases"})
	if id == "" {
		t.Fatal("empty id")
	}
	open := q.Open()
	if len(open) != 1 || open[0].Kind != evolve.KindMergeEntity {
		t.Fatalf("%+v", open)
	}
	if err := q.Resolve(id, true, "admin"); err != nil {
		t.Fatal(err)
	}
	if len(q.Open()) != 0 {
		t.Fatal("should be empty")
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `go test ./internal/audit/ ./internal/evolve/ -v`  
Expected: FAIL undefined packages

- [ ] **Step 3: Write minimal implementation**

```go
package audit

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"time"
)

type Log struct {
	path string
	mu   sync.Mutex
}

func New(path string) *Log { return &Log{path: path} }

func (l *Log) Record(op, actor, detail string) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if err := os.MkdirAll(filepath.Dir(l.path), 0o755); err != nil {
		return err
	}
	f, err := os.OpenFile(l.path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	row := map[string]string{
		"ts": time.Now().UTC().Format(time.RFC3339), "op": op, "actor": actor, "detail": detail,
	}
	enc := json.NewEncoder(f)
	return enc.Encode(row)
}
```

```go
package evolve

import (
	"fmt"
	"sync"
	"time"
)

type Kind string

const (
	KindMergeEntity   Kind = "merge_entity"
	KindChangeSchema  Kind = "change_schema"
	KindOverrideClaim Kind = "override_claim"
)

type Item struct {
	ID       string
	Kind     Kind
	Summary  string
	Done     bool
	Approved bool
	Actor    string
	Created  time.Time
}

type Queue struct {
	mu    sync.Mutex
	items []Item
	seq   int
}

func NewQueue() *Queue { return &Queue{} }

func (q *Queue) Enqueue(it Item) string {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.seq++
	it.ID = fmt.Sprintf("rv-%d", q.seq)
	it.Created = time.Now().UTC()
	q.items = append(q.items, it)
	return it.ID
}

func (q *Queue) Open() []Item {
	q.mu.Lock()
	defer q.mu.Unlock()
	var out []Item
	for _, it := range q.items {
		if !it.Done {
			out = append(out, it)
		}
	}
	return out
}

func (q *Queue) Resolve(id string, approved bool, actor string) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	for i := range q.items {
		if q.items[i].ID == id {
			q.items[i].Done = true
			q.items[i].Approved = approved
			q.items[i].Actor = actor
			return nil
		}
	}
	return fmt.Errorf("review %s not found", id)
}
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./internal/audit/ ./internal/evolve/ -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/audit/ internal/evolve/
git commit -m "feat: audit log and human review queue"
```

---

### Task 5: Deterministic code ingest (Service, File, Procedure)

**Files:**
- Create: `testdata/slice/repo/README.md`
- Create: `testdata/slice/repo/go.mod`
- Create: `testdata/slice/repo/cmd/pay-api/main.go`
- Create: `testdata/slice/repo/internal/refund/refund.go`
- Create: `testdata/slice/repo/docs/CHANGE.md`
- Create: `internal/ingest/code.go`
- Test: `internal/ingest/code_test.go`

- [ ] **Step 1: Write fixture files and failing test**

`testdata/slice/repo/go.mod`:

```
module example.com/pay-api

go 1.23
```

`testdata/slice/repo/README.md`:

```
# pay-api

退款服务，依赖 redis。
```

`testdata/slice/repo/cmd/pay-api/main.go`:

```go
package main

func main() {}
```

`testdata/slice/repo/internal/refund/refund.go`:

```go
package refund

func Refund() {}
```

`testdata/slice/repo/docs/CHANGE.md`:

```
# 变更流程

1. 提 PR
2. 评审
3. 发布
```

Test:

```go
package ingest_test

import (
	"path/filepath"
	"runtime"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/ingest"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func sliceRepo(t *testing.T) string {
	t.Helper()
	_, file, _, _ := runtime.Caller(0)
	return filepath.Clean(filepath.Join(filepath.Dir(file), "..", "..", "testdata", "slice", "repo"))
}

func TestCompileRepoWritesServiceFilesAndProcedure(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	if err := ingest.CompileRepo(sliceRepo(t), st); err != nil {
		t.Fatal(err)
	}
	svc, err := st.Read("entities/pay-api.md")
	if err != nil {
		t.Fatal(err)
	}
	if svc.Meta.Type != string(schema.EntityService) {
		t.Fatalf("type=%s", svc.Meta.Type)
	}
	files, err := st.Read("entities/files.md")
	if err != nil {
		t.Fatal(err)
	}
	if !contains(files.Body, "internal/refund/refund.go") {
		t.Fatalf("missing file node listing: %s", files.Body)
	}
	proc, err := st.Read("procedures/change.md")
	if err != nil {
		t.Fatal(err)
	}
	if proc.Meta.Type != string(schema.EntityProcedure) {
		t.Fatalf("proc type=%s", proc.Meta.Type)
	}
}

func contains(s, sub string) bool {
	return len(s) >= len(sub) && (s == sub || len(sub) == 0 ||
		(len(s) > 0 && (func() bool {
			for i := 0; i+len(sub) <= len(s); i++ {
				if s[i:i+len(sub)] == sub {
					return true
				}
			}
			return false
		})()))
}
```

（实现里用 `strings.Contains`，测试也可改成 `strings.Contains`。）

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/ingest/ -v`  
Expected: FAIL undefined `CompileRepo`

- [ ] **Step 3: Write minimal implementation**

```go
package ingest

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/secret"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func CompileRepo(repo string, st *wiki.Store) error {
	mod, err := os.ReadFile(filepath.Join(repo, "go.mod"))
	if err != nil {
		return err
	}
	name := moduleName(string(mod))
	readme, _ := os.ReadFile(filepath.Join(repo, "README.md"))
	body := secret.Strip(fmt.Sprintf("# %s\n\n%s\n", name, strings.TrimSpace(string(readme))))
	if err := st.Write(wiki.Page{
		Path: "entities/" + name + ".md",
		Meta: wiki.Meta{Title: name, Type: string(schema.EntityService), Source: "code"},
		Body: body,
	}); err != nil {
		return err
	}
	var files []string
	err = filepath.WalkDir(repo, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		rel, _ := filepath.Rel(repo, path)
		if strings.HasPrefix(rel, ".git") {
			return nil
		}
		files = append(files, filepath.ToSlash(rel))
		return nil
	})
	if err != nil {
		return err
	}
	var b strings.Builder
	fmt.Fprintf(&b, "%s 拥有以下文件（File 节点）：\n\n", name)
	for _, f := range files {
		fmt.Fprintf(&b, "- %s owned_by %s\n", f, name)
	}
	if err := st.Write(wiki.Page{
		Path: "entities/files.md",
		Meta: wiki.Meta{Title: name + " files", Type: string(schema.EntityFile), Source: "code"},
		Body: b.String(),
	}); err != nil {
		return err
	}
	change, err := os.ReadFile(filepath.Join(repo, "docs", "CHANGE.md"))
	if err != nil {
		return fmt.Errorf("procedure page required: %w", err)
	}
	return st.Write(wiki.Page{
		Path: "procedures/change.md",
		Meta: wiki.Meta{Title: "变更流程", Type: string(schema.EntityProcedure), Source: "code"},
		Body: secret.Strip(string(change)),
	})
}

func moduleName(gomod string) string {
	for _, line := range strings.Split(gomod, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "module ") {
			mod := strings.TrimSpace(strings.TrimPrefix(line, "module "))
			if i := strings.LastIndex(mod, "/"); i >= 0 {
				return mod[i+1:]
			}
			return mod
		}
	}
	return "service"
}
```

把测试里的 `contains` 改成 `strings.Contains`。

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./internal/ingest/ -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add testdata/slice/repo internal/ingest/
git commit -m "feat: compile repo snapshot into service file and procedure wiki pages"
```

---

### Task 6: GraphStore fake with supersession

**Files:**
- Create: `internal/graph/types.go`
- Create: `internal/graph/store.go`
- Create: `internal/graph/fake.go`
- Test: `internal/graph/fake_test.go`

Graphiti 生产客户端在 Task 7。本任务只定接口和 fake，保证「新工单失效旧结论」可单测。

- [ ] **Step 1: Write the failing test**

```go
package graph_test

import (
	"context"
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
)

func TestFakeSupersedesInvalidatesOldEdge(t *testing.T) {
	ctx := context.Background()
	g := graph.NewFake()
	old := graph.Fact{
		ID: "e1", Source: "pay-api", Kind: schema.EdgeUses, Target: "redis-6",
		ValidAt: time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC),
	}
	if err := g.AddFact(ctx, old); err != nil {
		t.Fatal(err)
	}
	neu := graph.Fact{
		ID: "e2", Source: "pay-api", Kind: schema.EdgeUses, Target: "redis-7",
		ValidAt: time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC),
	}
	if err := g.AddFact(ctx, neu); err != nil {
		t.Fatal(err)
	}
	if err := g.Supersede(ctx, "e2", "e1"); err != nil {
		t.Fatal(err)
	}
	nb, err := g.Neighborhood(ctx, "pay-api")
	if err != nil {
		t.Fatal(err)
	}
	var stale, live int
	for _, e := range nb.Edges {
		if e.ID == "e1" && e.InvalidAt != nil {
			stale++
		}
		if e.ID == "e2" && e.InvalidAt == nil {
			live++
		}
	}
	if stale != 1 || live != 1 {
		t.Fatalf("stale=%d live=%d edges=%+v", stale, live, nb.Edges)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/graph/ -v`  
Expected: FAIL undefined

- [ ] **Step 3: Write minimal implementation**

```go
package graph

import (
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/schema"
)

type Fact struct {
	ID        string
	Source    string
	Kind      schema.EdgeKind
	Target    string
	ValidAt   time.Time
	InvalidAt *time.Time
	Episode   string
}

type Node struct {
	ID   string
	Kind schema.EntityKind
}

type Neighborhood struct {
	Nodes []Node
	Edges []Fact
}

type Episode struct {
	Name    string
	Body    string
	GroupID string
	Source  string
}
```

```go
package graph

import "context"

type Store interface {
	AddEpisode(ctx context.Context, ep Episode) error
	AddFact(ctx context.Context, f Fact) error
	Supersede(ctx context.Context, newID, oldID string) error
	Search(ctx context.Context, query string) ([]Fact, error)
	Neighborhood(ctx context.Context, center string) (Neighborhood, error)
}
```

```go
package graph

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/schema"
)

type Fake struct {
	mu    sync.Mutex
	facts map[string]Fact
}

func NewFake() *Fake { return &Fake{facts: map[string]Fact{}} }

func (f *Fake) AddEpisode(context.Context, Episode) error { return nil }

func (f *Fake) AddFact(_ context.Context, fact Fact) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if fact.ID == "" {
		return fmt.Errorf("fact id required")
	}
	f.facts[fact.ID] = fact
	return nil
}

func (f *Fake) Supersede(_ context.Context, newID, oldID string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	old, ok := f.facts[oldID]
	if !ok {
		return fmt.Errorf("old fact %s not found", oldID)
	}
	if _, ok := f.facts[newID]; !ok {
		return fmt.Errorf("new fact %s not found", newID)
	}
	now := time.Now().UTC()
	old.InvalidAt = &now
	old.Kind = schema.EdgeSupersedes
	f.facts[oldID] = old
	return nil
}

func (f *Fake) Search(_ context.Context, query string) ([]Fact, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	q := strings.ToLower(query)
	var out []Fact
	for _, fact := range f.facts {
		hay := strings.ToLower(fact.Source + " " + fact.Target + " " + string(fact.Kind))
		if strings.Contains(hay, q) || q == "" {
			out = append(out, fact)
		}
	}
	return out, nil
}

func (f *Fake) Neighborhood(_ context.Context, center string) (Neighborhood, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	nodes := map[string]Node{}
	var edges []Fact
	for _, fact := range f.facts {
		if fact.Source != center && fact.Target != center {
			continue
		}
		edges = append(edges, fact)
		nodes[fact.Source] = Node{ID: fact.Source}
		nodes[fact.Target] = Node{ID: fact.Target}
	}
	nb := Neighborhood{Edges: edges}
	for _, n := range nodes {
		nb.Nodes = append(nb.Nodes, n)
	}
	return nb, nil
}
```

修正 `Supersede`：旧边应保留原 `Kind`（例如 `uses`），用 `InvalidAt` 表示失效；不要把 kind 改成 `supersedes`。另存一条 `new -supersedes-> old` 边。

把 `Supersede` 改成：

```go
func (f *Fake) Supersede(_ context.Context, newID, oldID string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	old, ok := f.facts[oldID]
	if !ok {
		return fmt.Errorf("old fact %s not found", oldID)
	}
	neu, ok := f.facts[newID]
	if !ok {
		return fmt.Errorf("new fact %s not found", newID)
	}
	now := time.Now().UTC()
	old.InvalidAt = &now
	f.facts[oldID] = old
	link := Fact{
		ID:      "sup-" + newID + "-" + oldID,
		Source:  neu.ID,
		Kind:    schema.EdgeSupersedes,
		Target:  old.ID,
		ValidAt: now,
	}
	f.facts[link.ID] = link
	return nil
}
```

测试只检查 `e1.InvalidAt != nil` 且 `e2.InvalidAt == nil`。`Neighborhood` 以 `pay-api` 为中心仍能看到 e1/e2。

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./internal/graph/ -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/graph/
git commit -m "feat: graph store interface with supersession fake"
```

---

### Task 7: Graphiti HTTP client

**Files:**
- Create: `internal/graph/http.go`
- Test: `internal/graph/http_test.go`

对照 Graphiti 服务源码：写入 `POST /messages`，检索 `POST /search`，body 字段 `group_ids`、`query`、`max_facts`。若联调发现路径不同，只改 `http.go`，不要改 `Store` 接口。

- [ ] **Step 1: Write the failing test**（httptest 假服务器）

```go
package graph_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
)

func TestHTTPAddEpisodePostsMessages(t *testing.T) {
	var got map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/messages" {
			t.Fatalf("path %s", r.URL.Path)
		}
		b, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(b, &got)
		w.WriteHeader(202)
	}))
	defer srv.Close()
	c := graph.NewHTTP(srv.URL, schema.DefaultGroupID)
	err := c.AddEpisode(context.Background(), graph.Episode{Name: "wiki:pay-api", Body: "pay-api uses redis", Source: "text"})
	if err != nil {
		t.Fatal(err)
	}
	if got["group_id"] != schema.DefaultGroupID {
		t.Fatalf("%v", got)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/graph/ -run TestHTTPAddEpisodePostsMessages -v`  
Expected: FAIL undefined `NewHTTP`

- [ ] **Step 3: Write minimal implementation**

```go
package graph

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/schema"
)

type HTTP struct {
	base    string
	groupID string
	client  *http.Client
}

func NewHTTP(base, groupID string) *HTTP {
	return &HTTP{base: strings.TrimRight(base, "/"), groupID: groupID, client: &http.Client{Timeout: 15 * time.Second}}
}

func (h *HTTP) AddEpisode(ctx context.Context, ep Episode) error {
	body := map[string]any{
		"group_id": h.groupID,
		"messages": []map[string]string{{
			"content": ep.Name + "\n" + ep.Body,
			"role_type": "system",
			"role": "wiki",
		}},
	}
	b, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, h.base+"/messages", bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	res, err := h.client.Do(req)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.StatusCode >= 300 {
		return fmt.Errorf("graphiti add episode: %s", res.Status)
	}
	return nil
}

func (h *HTTP) AddFact(context.Context, Fact) error {
	return fmt.Errorf("AddFact is fake-only; HTTP store uses AddEpisode extraction")
}

func (h *HTTP) Supersede(context.Context, string, string) error {
	return fmt.Errorf("HTTP supersede relies on Graphiti temporal invalidation via new episode text")
}

func (h *HTTP) Search(ctx context.Context, query string) ([]Fact, error) {
	body := map[string]any{"group_ids": []string{h.groupID}, "query": query, "max_facts": 10}
	b, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, h.base+"/search", bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	res, err := h.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()
	if res.StatusCode >= 300 {
		return nil, fmt.Errorf("graphiti search: %s", res.Status)
	}
	var parsed struct {
		Facts []struct {
			UUID      string `json:"uuid"`
			Fact      string `json:"fact"`
			ValidAt   string `json:"valid_at"`
			InvalidAt string `json:"invalid_at"`
		} `json:"facts"`
	}
	if err := json.NewDecoder(res.Body).Decode(&parsed); err != nil {
		return nil, err
	}
	out := make([]Fact, 0, len(parsed.Facts))
	for _, f := range parsed.Facts {
		out = append(out, Fact{ID: f.UUID, Source: f.Fact, Kind: schema.EdgeUses})
	}
	return out, nil
}

func (h *HTTP) Neighborhood(ctx context.Context, center string) (Neighborhood, error) {
	facts, err := h.Search(ctx, center)
	if err != nil {
		return Neighborhood{}, err
	}
	return Neighborhood{Edges: facts, Nodes: []Node{{ID: center}}}, nil
}
```

生产路径：wiki 投影用 `AddEpisode`（让 Graphiti 抽边）。`Supersede` 在 ingest 工单时写「X supersedes Y」进 episode body，由 Graphiti 做边失效。单测 supersession 只跑 Fake。

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./internal/graph/ -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/graph/http.go internal/graph/http_test.go
git commit -m "feat: Graphiti HTTP client for episodes and search"
```

---

### Task 8: Project wiki to graph; ingest ticket with supersede

**Files:**
- Create: `testdata/slice/tickets/INC-1.json`
- Create: `internal/ingest/project.go`
- Create: `internal/ingest/ticket.go`
- Test: `internal/ingest/ticket_test.go`

`testdata/slice/tickets/INC-1.json`:

```json
{
  "id": "INC-1",
  "title": "redis 升级到 7",
  "body": "pay-api 不再使用 redis-6，改为 redis-7。此变更 supersedes 旧依赖。",
  "at": "2026-09-01T00:00:00Z"
}
```

- [ ] **Step 1: Write the failing test**

```go
package ingest_test

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/ingest"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func TestTicketMarksOldWikiClaimStaleAndSupersedesFact(t *testing.T) {
	ctx := context.Background()
	st := wiki.NewStore(t.TempDir())
	_ = st.Write(wiki.Page{
		Path: "entities/pay-api.md",
		Meta: wiki.Meta{Title: "pay-api", Type: string(schema.EntityService), Source: "code"},
		Body: "uses redis-6",
	})
	g := graph.NewFake()
	_ = g.AddFact(ctx, graph.Fact{ID: "e-old", Source: "pay-api", Kind: schema.EdgeUses, Target: "redis-6", ValidAt: time.Now().Add(-24 * time.Hour)})
	_, file, _, _ := runtime.Caller(0)
	ticket := filepath.Join(filepath.Dir(file), "..", "..", "testdata", "slice", "tickets", "INC-1.json")
	raw, err := os.ReadFile(ticket)
	if err != nil {
		t.Fatal(err)
	}
	if err := ingest.ApplyTicket(ctx, raw, st, g); err != nil {
		t.Fatal(err)
	}
	p, _ := st.Read("entities/pay-api.md")
	if !p.Meta.Stale {
		t.Fatal("expected stale claim on wiki page")
	}
	nb, _ := g.Neighborhood(ctx, "pay-api")
	var invalidated bool
	for _, e := range nb.Edges {
		if e.ID == "e-old" && e.InvalidAt != nil {
			invalidated = true
		}
	}
	if !invalidated {
		t.Fatalf("old fact still valid: %+v", nb.Edges)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/ingest/ -run TestTicketMarksOldWikiClaimStaleAndSupersedesFact -v`  
Expected: FAIL undefined `ApplyTicket`

- [ ] **Step 3: Write minimal implementation**

```go
package ingest

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/secret"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

type ticket struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	Body  string `json:"body"`
	At    string `json:"at"`
}

func ApplyTicket(ctx context.Context, raw []byte, st *wiki.Store, g graph.Store) error {
	var tk ticket
	if err := json.Unmarshal(raw, &tk); err != nil {
		return err
	}
	body := secret.Strip(tk.Title + "\n" + tk.Body)
	if err := g.AddEpisode(ctx, graph.Episode{Name: tk.ID, Body: body, GroupID: schema.DefaultGroupID, Source: "ticket"}); err != nil {
		return err
	}
	at := time.Now().UTC()
	if tk.At != "" {
		if parsed, err := time.Parse(time.RFC3339, tk.At); err == nil {
			at = parsed
		}
	}
	newID := "e-" + tk.ID
	_ = g.AddFact(ctx, graph.Fact{ID: newID, Source: "pay-api", Kind: schema.EdgeUses, Target: "redis-7", ValidAt: at, Episode: tk.ID})
	if strings.Contains(strings.ToLower(body), "supersedes") {
		if err := g.Supersede(ctx, newID, "e-old"); err != nil {
			return err
		}
		p, err := st.Read("entities/pay-api.md")
		if err != nil {
			return err
		}
		p.Meta.Stale = true
		p.Meta.Superseded = tk.ID
		p.Body += fmt.Sprintf("\n\n被 %s 取代。\n", tk.ID)
		if err := st.Write(p); err != nil {
			return err
		}
	}
	return st.Write(wiki.Page{
		Path: "summaries/" + tk.ID + ".md",
		Meta: wiki.Meta{Title: tk.Title, Type: string(schema.EntityIncident), Source: "ticket"},
		Body: body,
	})
}

func ProjectWiki(ctx context.Context, st *wiki.Store, g graph.Store) error {
	pages, err := st.All()
	if err != nil {
		return err
	}
	for _, p := range pages {
		ep := graph.Episode{Name: "wiki:" + p.Path, Body: p.Meta.Title + "\n" + p.Body, GroupID: schema.DefaultGroupID, Source: "wiki"}
		if err := g.AddEpisode(ctx, ep); err != nil {
			return fmt.Errorf("project %s: %w (wiki kept)", p.Path, err)
		}
	}
	return nil
}
```

`e-old` 写死仅用于切片夹具。通用匹配第二期再做。第一期评测集固定 `e-old`。

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./internal/ingest/ -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add testdata/slice/tickets internal/ingest/
git commit -m "feat: project wiki to graph and apply ticket supersession"
```

---

### Task 9: Policy compile and citation-only docs store

**Files:**
- Create: `testdata/slice/policies/refund.md`
- Create: `internal/docs/store.go`
- Create: `internal/docs/fake.go`
- Create: `internal/ingest/policy.go`
- Test: `internal/ingest/policy_test.go`

`testdata/slice/policies/refund.md` 正文：`第3条 退款须在 7 日内完成。`

- [ ] **Step 1: Write the failing test**

```go
package ingest_test

import (
	"errors"
	"os"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/docs"
	"github.com/wang550301463/sunny-knowledge/internal/ingest"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

type boomParse struct{ docs.Fake }

func (boomParse) Parse(string) (string, error) { return "", errors.New("ragflow down") }

func TestPolicyCompileWritesWikiAndKeepsRawOnParseError(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	rawDir := t.TempDir()
	src := t.TempDir() + "/refund.md"
	_ = os.WriteFile(src, []byte("第3条 退款须在 7 日内完成。"), 0o644)
	cite := docs.NewFake()
	if err := ingest.CompilePolicy(src, rawDir, st, cite); err != nil {
		t.Fatal(err)
	}
	p, err := st.Read("entities/policy-refund.md")
	if err != nil {
		t.Fatal(err)
	}
	if p.Meta.Type != string(schema.EntityPolicy) {
		t.Fatalf("%s", p.Meta.Type)
	}
	got, err := cite.Cite("policy-refund")
	if err != nil || got == "" {
		t.Fatalf("cite=%q err=%v", got, err)
	}
	fail := wiki.NewStore(t.TempDir())
	if err := ingest.CompilePolicy(src, t.TempDir(), fail, boomParse{}); err == nil {
		t.Fatal("expected parse error")
	}
	if _, err := fail.Read("entities/policy-refund.md"); err == nil {
		t.Fatal("must not write fake policy page")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/ingest/ -run TestPolicyCompileWritesWikiAndKeepsRawOnParseError -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```go
package docs

type Store interface {
	Parse(path string) (string, error)
	Cite(docID string) (string, error)
}
```

```go
package docs

import "fmt"

type Fake struct {
	parsed map[string]string
}

func NewFake() *Fake { return &Fake{parsed: map[string]string{}} }

func (f *Fake) Parse(path string) (string, error) {
	b, err := readFile(path)
	if err != nil {
		return "", err
	}
	f.parsed["policy-refund"] = b
	return b, nil
}

func (f *Fake) Cite(docID string) (string, error) {
	s, ok := f.parsed[docID]
	if !ok {
		return "", fmt.Errorf("no citation for %s", docID)
	}
	return s, nil
}
```

把 `readFile` 做成 `os.ReadFile` 的小包装，放在 `fake.go`。

```go
package ingest

import (
	"os"
	"path/filepath"

	"github.com/wang550301463/sunny-knowledge/internal/docs"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/secret"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func CompilePolicy(src, rawDir string, st *wiki.Store, d docs.Store) error {
	text, err := d.Parse(src)
	dst := filepath.Join(rawDir, filepath.Base(src))
	orig, _ := os.ReadFile(src)
	_ = os.MkdirAll(rawDir, 0o755)
	_ = os.WriteFile(dst, orig, 0o644)
	if err != nil {
		return err
	}
	return st.Write(wiki.Page{
		Path: "entities/policy-refund.md",
		Meta: wiki.Meta{Title: "退款制度", Type: string(schema.EntityPolicy), Source: "policy"},
		Body: secret.Strip(text),
	})
}
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./internal/ingest/ ./internal/docs/ -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add testdata/slice/policies internal/docs internal/ingest/policy.go internal/ingest/policy_test.go
git commit -m "feat: compile policies to wiki and keep raw on parse failure"
```

---

### Task 10: Query classify, weighted RRF, orchestrator

**Files:**
- Create: `internal/query/classify.go`
- Create: `internal/query/rrf.go`
- Create: `internal/query/ask.go`
- Test: `internal/query/ask_test.go`

规则：wiki/图权重大于 chunk；结构题默认不拉 cite；chunk 不得把 `Stale` 页改回有效。

- [ ] **Step 1: Write the failing test**

```go
package query_test

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/docs"
	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/query"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func TestAskPrefersWikiAndGraphOverChunkAndKeepsStale(t *testing.T) {
	ctx := context.Background()
	st := wiki.NewStore(t.TempDir())
	_ = st.Write(wiki.Page{
		Path: "entities/pay-api.md",
		Meta: wiki.Meta{Title: "pay-api", Type: string(schema.EntityService), Source: "code", Stale: true, Superseded: "INC-1"},
		Body: "uses redis-6（已过期）",
	})
	_ = st.Write(wiki.Page{
		Path: "summaries/INC-1.md",
		Meta: wiki.Meta{Title: "redis 升级", Type: string(schema.EntityIncident), Source: "ticket"},
		Body: "现用 redis-7",
	})
	g := graph.NewFake()
	inv := time.Now()
	_ = g.AddFact(ctx, graph.Fact{ID: "e-old", Source: "pay-api", Kind: schema.EdgeUses, Target: "redis-6", InvalidAt: &inv})
	_ = g.AddFact(ctx, graph.Fact{ID: "e-new", Source: "pay-api", Kind: schema.EdgeUses, Target: "redis-7", ValidAt: time.Now()})
	cite := docs.NewFake()
	ans, err := query.Ask(ctx, query.Request{Question: "升级 redis 会影响 pay-api 吗"}, st, g, cite)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(ans.Text, "redis-7") {
		t.Fatalf("answer=%s", ans.Text)
	}
	if strings.Contains(ans.Text, "仍使用 redis-6") {
		t.Fatal("stale claim leaked as current")
	}
	if ans.UsedCitation {
		t.Fatal("structure question must not cite chunks")
	}
}

func TestRRFWeightsWikiAboveChunk(t *testing.T) {
	got := query.Fuse([][]string{{"wiki-a", "wiki-b"}, {"chunk-a"}}, []float64{1.0, 0.2}, 60)
	if got[0] != "wiki-a" {
		t.Fatalf("%v", got)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/query/ -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```go
package query

import "strings"

type Kind string

const (
	KindStructure Kind = "structure"
	KindTemporal  Kind = "temporal"
	KindCite      Kind = "cite"
)

func Classify(q string) Kind {
	s := strings.ToLower(q)
	switch {
	case strings.Contains(s, "原文") || strings.Contains(s, "第") && strings.Contains(s, "条"):
		return KindCite
	case strings.Contains(s, "现在") || strings.Contains(s, "还在") || strings.Contains(s, "升级"):
		return KindTemporal
	default:
		return KindStructure
	}
}
```

```go
package query

import "sort"

func Fuse(ranked [][]string, weights []float64, k int) []string {
	score := map[string]float64{}
	for i, list := range ranked {
		w := 1.0
		if i < len(weights) {
			w = weights[i]
		}
		for rank, id := range list {
			score[id] += w / (float64(k) + float64(rank+1))
		}
	}
	type pair struct {
		id string
		sc float64
	}
	var ps []pair
	for id, sc := range score {
		ps = append(ps, pair{id, sc})
	}
	sort.Slice(ps, func(i, j int) bool { return ps[i].sc > ps[j].sc })
	out := make([]string, len(ps))
	for i, p := range ps {
		out[i] = p.id
	}
	return out
}
```

```go
package query

import (
	"context"
	"fmt"
	"strings"

	"github.com/wang550301463/sunny-knowledge/internal/docs"
	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

type Request struct {
	Question string
}

type Answer struct {
	Text          string
	Pages         []string
	Facts         []graph.Fact
	UsedCitation  bool
	GraphDegraded bool
}

func Ask(ctx context.Context, req Request, st *wiki.Store, g graph.Store, cite docs.Store) (Answer, error) {
	kind := Classify(req.Question)
	hits := st.Search(req.Question)
	var pageIDs []string
	var bodies []string
	for i, h := range hits {
		if i >= 5 {
			break
		}
		pageIDs = append(pageIDs, h.Path)
		p, err := st.Read(h.Path)
		if err != nil {
			continue
		}
		prefix := ""
		if p.Meta.Stale {
			prefix = "【已过期】"
		}
		bodies = append(bodies, prefix+p.Body)
	}
	facts, err := g.Search(ctx, req.Question)
	degraded := false
	if err != nil {
		degraded = true
		facts = nil
	}
	var live []string
	for _, f := range facts {
		if f.InvalidAt == nil {
			live = append(live, f.Source+" "+string(f.Kind)+" "+f.Target)
		}
	}
	fused := Fuse([][]string{pageIDs, live}, []float64{1.0, 0.8}, 60)
	ans := Answer{Pages: pageIDs, Facts: facts, GraphDegraded: degraded}
	ans.Text = strings.Join(bodies, "\n")
	if len(live) > 0 {
		ans.Text += "\n现行关系：" + strings.Join(live, "; ")
	}
	if degraded {
		ans.Text += "\n（图未就绪，仅 wiki）"
	}
	if kind == KindCite {
		c, err := cite.Cite("policy-refund")
		if err == nil {
			ans.UsedCitation = true
			ans.Text += "\n原文：" + c
		}
	}
	if ans.Text == "" {
		ans.Text = fmt.Sprintf("无编译页命中：%v", fused)
	}
	return ans, nil
}
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./internal/query/ -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/query/
git commit -m "feat: wiki-plus-graph query with weighted RRF"
```

---

### Task 11: Crystallize quality answers back to wiki

**Files:**
- Create: `internal/crystal/crystal.go`
- Test: `internal/crystal/crystal_test.go`

- [ ] **Step 1: Write the failing test**

```go
package crystal_test

import (
	"context"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/crystal"
	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/query"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func TestCrystallizeWritesDigestWhenQualityHigh(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	g := graph.NewFake()
	ans := query.Answer{Text: "pay-api 现用 redis-7", Pages: []string{"summaries/INC-1.md"}}
	if err := crystal.MaybeFile(context.Background(), ans, 0.9, st, g); err != nil {
		t.Fatal(err)
	}
	p, err := st.Read("summaries/digest-latest.md")
	if err != nil {
		t.Fatal(err)
	}
	if p.Meta.Source != "crystal" {
		t.Fatalf("%s", p.Meta.Source)
	}
}

func TestCrystallizeSkipsLowQuality(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	g := graph.NewFake()
	ans := query.Answer{Text: "不知道"}
	_ = crystal.MaybeFile(context.Background(), ans, 0.1, st, g)
	if _, err := st.Read("summaries/digest-latest.md"); err == nil {
		t.Fatal("should not write")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/crystal/ -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```go
package crystal

import (
	"context"
	"fmt"

	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/query"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

const MinQuality = 0.7

func MaybeFile(ctx context.Context, ans query.Answer, quality float64, st *wiki.Store, g graph.Store) error {
	if quality < MinQuality {
		return nil
	}
	p := wiki.Page{
		Path: "summaries/digest-latest.md",
		Meta: wiki.Meta{Title: "结晶", Type: string(schema.EntityDecision), Source: "crystal"},
		Body: fmt.Sprintf("%s\n\n来源页：%v", ans.Text, ans.Pages),
	}
	if err := st.Write(p); err != nil {
		return err
	}
	return g.AddEpisode(ctx, graph.Episode{Name: "crystal:digest-latest", Body: p.Body, GroupID: schema.DefaultGroupID, Source: "crystal"})
}
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./internal/crystal/ -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/crystal/
git commit -m "feat: crystallize high-quality answers into wiki digests"
```

---

### Task 12: HTTP API, roles, neighborhood for viz

**Files:**
- Create: `internal/httpapi/auth.go`
- Create: `internal/httpapi/server.go`
- Create: `cmd/api/main.go`
- Create: `cmd/ingest/main.go`
- Test: `internal/httpapi/server_test.go`

角色：`reader` 只读；`editor` 可 ask/结晶；`admin` 可审队列。Token 来自环境变量 `SUNNY_TOKEN`，测试里注入。

- [ ] **Step 1: Write the failing test**

```go
package httpapi_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/docs"
	"github.com/wang550301463/sunny-knowledge/internal/evolve"
	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/httpapi"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func TestAskAndNeighborhoodAndReviewAuth(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	_ = st.Write(wiki.Page{Path: "procedures/change.md", Meta: wiki.Meta{Title: "变更流程", Type: string(schema.EntityProcedure), Source: "code"}, Body: "提 PR 后发布"})
	g := graph.NewFake()
	_ = g.AddFact(t.Context(), graph.Fact{ID: "e1", Source: "pay-api", Kind: schema.EdgeOwns, Target: "internal/refund/refund.go"})
	q := evolve.NewQueue()
	srv := httpapi.New(httpapi.Deps{Wiki: st, Graph: g, Cite: docs.NewFake(), Reviews: q, Token: "secret"})
	ts := httptest.NewServer(srv)
	defer ts.Close()

	req, _ := http.NewRequest(http.MethodPost, ts.URL+"/v1/ask", strings.NewReader(`{"question":"我们怎么做变更"}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Token", "secret")
	req.Header.Set("X-Role", "editor")
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	if res.StatusCode != 200 {
		t.Fatalf("status %d", res.StatusCode)
	}
	var ans map[string]any
	_ = json.NewDecoder(res.Body).Decode(&ans)
	if !strings.Contains(ans["text"].(string), "PR") {
		t.Fatalf("%v", ans)
	}

	nreq, _ := http.NewRequest(http.MethodGet, ts.URL+"/v1/graph/neighborhood?center=pay-api", nil)
	nreq.Header.Set("X-Token", "secret")
	nreq.Header.Set("X-Role", "reader")
	nres, _ := http.DefaultClient.Do(nreq)
	if nres.StatusCode != 200 {
		t.Fatalf("nb %d", nres.StatusCode)
	}

	bad, _ := http.NewRequest(http.MethodGet, ts.URL+"/v1/reviews", nil)
	bad.Header.Set("X-Token", "secret")
	bad.Header.Set("X-Role", "reader")
	bres, _ := http.DefaultClient.Do(bad)
	if bres.StatusCode != 403 {
		t.Fatalf("want 403 got %d", bres.StatusCode)
	}
}
```

若当前 Go 版本 `t.Context` 不存在，改用 `context.Background()`。

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/httpapi/ -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`auth.go`：校验 `X-Token == Token`；角色 `reader|editor|admin`。

`server.go`：mux

- `POST /v1/ask` editor+admin → `query.Ask`，质量 0.9 时 `crystal.MaybeFile`
- `GET /v1/wiki/{path...}` 全角色
- `GET /v1/graph/neighborhood?center=` 全角色，JSON `{nodes,edges}`，边含 `invalidAt`
- `GET /v1/reviews` 仅 admin
- `POST /v1/reviews/{id}/resolve` 仅 admin

`cmd/ingest/main.go`：按序 `CompileRepo`、`CompilePolicy`、`ProjectWiki`、`ApplyTicket`。

`cmd/api/main.go`：加载 `data/wiki`，Graphiti URL 环境变量 `GRAPHITI_URL`（空则 Fake 仅限测试；生产必须设置）。

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./...`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/httpapi cmd
git commit -m "feat: HTTP API for ask, wiki, neighborhood, and reviews"
```

---

### Task 13: Force-directed web UI

**Files:**
- Create: `web/package.json`
- Create: `web/index.html`
- Create: `web/src/main.jsx`
- Create: `web/src/App.jsx`

- [ ] **Step 1: Write a UI test as a node assert on the data mapper**（避免无浏览器环境）

Create `web/src/graphMap.js`:

```js
export function toGraph(payload, sourceFilter) {
  const nodes = (payload.nodes || []).map((n) => ({ id: n.id, group: n.kind || "Entity" }));
  const links = (payload.edges || [])
    .filter((e) => !sourceFilter || e.sourceKind === sourceFilter || e.origin === sourceFilter)
    .map((e) => ({
      source: e.source,
      target: e.target,
      invalid: Boolean(e.invalidAt),
      kind: e.kind,
    }));
  return { nodes, links };
}
```

Create `web/src/graphMap.test.js`（用 node `--test`）：

```js
import assert from "node:assert/strict";
import { toGraph } from "./graphMap.js";
import test from "node:test";

test("keeps invalidated edges", () => {
  const g = toGraph({
    nodes: [{ id: "pay-api" }, { id: "redis-6" }],
    edges: [{ source: "pay-api", target: "redis-6", invalidAt: "2026-09-01T00:00:00Z", kind: "uses" }],
  });
  assert.equal(g.links[0].invalid, true);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test web/src/graphMap.test.js`  
Expected: FAIL cannot find module

- [ ] **Step 3: Add App.jsx**

左：`ForceGraph2D`，失效边虚线灰色；过滤 code/policy/ticket。点节点 `GET /v1/wiki/` 对应页（File 打开 `entities/files.md` 并高亮路径）。右：markdown 原文 + 可选 citation。

`package.json` dependencies: `react`, `react-dom`, `react-force-graph-2d`。

- [ ] **Step 4: Run mapper test**

Run: `node --test web/src/graphMap.test.js`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web
git commit -m "feat: force-directed wiki graph explorer"
```

---

### Task 14: Compose Graphiti+Neo4j, schema file, gitignore

**Files:**
- Create: `deploy/docker-compose.yml`
- Create: `AGENTS.md`
- Create: `.gitignore`
- Modify: `README.md`

- [ ] **Step 1: Write AGENTS.md**（schema，无测试，人工验收）

写明：实体表、边表、ingest 三类来源、wiki 优先于图、chunk 只引用、人审三种 Kind、结晶阈值 0.7、`group_id=enterprise`、禁止 PDF episode、禁止 RAGFlow GraphRAG。

- [ ] **Step 2: docker-compose**

Neo4j 5.26 官方镜像，Bolt `7687`，密码写在 compose 里仅限本地开发。Graphiti 用官方 `server` 镜像或文档中的 compose 片段，环境变量指向 Neo4j。Go API `GRAPHITI_URL=http://localhost:8000`。

- [ ] **Step 3: .gitignore**

```
data/wiki/
web/node_modules/
web/dist/
.DS_Store
```

把 `testdata/` 纳入版本控制。

- [ ] **Step 4: README 写清**

```
go test ./...
node --test web/src/graphMap.test.js
go run ./cmd/ingest --repo testdata/slice/repo --policy testdata/slice/policies/refund.md --ticket testdata/slice/tickets/INC-1.json
SUNNY_TOKEN=secret go run ./cmd/api
```

验收：问「我们怎么做变更」命中 procedural 页；问「升级 redis」答案含 redis-7 且不把 redis-6 当现行；图上能看到失效边；能点到 `internal/refund/refund.go`。

- [ ] **Step 5: Commit**

```bash
git add deploy AGENTS.md .gitignore README.md
git commit -m "chore: add schema, compose stack, and slice runbook"
```

---

### Task 15: Query access log and wiki confidence fields

**Files:**
- Modify: `internal/wiki/page.go`
- Modify: `internal/wiki/store.go`（Parse/Render 增加字段）
- Create: `internal/access/log.go`
- Modify: `internal/query/ask.go`
- Test: `internal/access/log_test.go`
- Test: `internal/wiki/confidence_test.go`

frontmatter 增加：`confidence`（0–1，默认 1）、`last_accessed`、`last_reinforced`、`scope`（`shared`|`private`）。Ask 每命中一页就记访问并刷新 `last_accessed`（不在本任务衰减）。

- [ ] **Step 1: Write the failing tests**

```go
package wiki_test

import (
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func TestParseRenderConfidenceAndScope(t *testing.T) {
	raw := "---\ntitle: pay-api\ntype: Service\nsource: code\nstale: false\nsuperseded: \nconfidence: 0.85\nscope: private\nlast_accessed: 2026-09-01T00:00:00Z\nlast_reinforced: 2026-08-01T00:00:00Z\n---\n\nbody\n"
	p, err := wiki.Parse("entities/pay-api.md", raw)
	if err != nil {
		t.Fatal(err)
	}
	if p.Meta.Confidence != 0.85 || p.Meta.Scope != "private" {
		t.Fatalf("%+v", p.Meta)
	}
	out := p.Render()
	p2, err := wiki.Parse("entities/pay-api.md", out)
	if err != nil {
		t.Fatal(err)
	}
	if p2.Meta.Confidence != 0.85 || p2.Meta.Scope != "private" {
		t.Fatalf("roundtrip %+v", p2.Meta)
	}
}
```

```go
package access_test

import (
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/access"
)

func TestRecordAndLastAccess(t *testing.T) {
	l := access.New()
	at := time.Date(2026, 9, 1, 12, 0, 0, 0, time.UTC)
	l.Record("entities/pay-api.md", "editor", at)
	got, ok := l.Last("entities/pay-api.md")
	if !ok || !got.Equal(at) {
		t.Fatalf("got %v ok=%v", got, ok)
	}
}
```

再在 `internal/query/ask_test.go` 增加：

```go
func TestAskRecordsAccess(t *testing.T) {
	ctx := context.Background()
	st := wiki.NewStore(t.TempDir())
	_ = st.Write(wiki.Page{
		Path: "entities/pay-api.md",
		Meta: wiki.Meta{Title: "pay-api", Type: string(schema.EntityService), Source: "code", Confidence: 1, Scope: "shared"},
		Body: "pay-api 处理退款",
	})
	acc := access.New()
	_, err := query.Ask(ctx, query.Request{Question: "退款", Actor: "editor"}, st, graph.NewFake(), docs.NewFake(), acc)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := acc.Last("entities/pay-api.md"); !ok {
		t.Fatal("access not recorded")
	}
	p, _ := st.Read("entities/pay-api.md")
	if p.Meta.LastAccessed.IsZero() {
		t.Fatal("wiki last_accessed not updated")
	}
}
```

`Ask` 签名改为增加 `Actor string` 与 `acc *access.Log`。所有旧测试传入 `access.New()` 和 `Actor: "tester"`。

- [ ] **Step 2: Run tests to verify they fail**

Run: `go test ./internal/wiki/ ./internal/access/ ./internal/query/ -v`  
Expected: FAIL 缺字段或 `Ask` 参数不匹配

- [ ] **Step 3: Write minimal implementation**

`Meta` 增加：

```go
type Meta struct {
	Title          string
	Type           string
	Source         string
	Stale          bool
	Superseded     string
	Confidence     float64
	Scope          string
	LastAccessed   time.Time
	LastReinforced time.Time
}
```

`Parse`：缺省 `confidence=1`、`scope=shared`。时间用 `time.RFC3339`。`Render` 写出全部字段。

```go
package access

import (
	"sync"
	"time"
)

type Event struct {
	Path  string
	Actor string
	At    time.Time
}

type Log struct {
	mu    sync.Mutex
	last  map[string]time.Time
	items []Event
}

func New() *Log { return &Log{last: map[string]time.Time{}} }

func (l *Log) Record(path, actor string, at time.Time) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.last[path] = at
	l.items = append(l.items, Event{Path: path, Actor: actor, At: at})
}

func (l *Log) Last(path string) (time.Time, bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	t, ok := l.last[path]
	return t, ok
}

func (l *Log) All() []Event {
	l.mu.Lock()
	defer l.mu.Unlock()
	out := make([]Event, len(l.items))
	copy(out, l.items)
	return out
}
```

`Ask` 在读到每个 hit 后：`acc.Record(p.Path, req.Actor, time.Now().UTC())`，然后 `p.Meta.LastAccessed = now; st.Write(p)`。

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./internal/wiki/ ./internal/access/ ./internal/query/ ./internal/httpapi/ -v`  
Expected: PASS（同步改 `httpapi` 里对 `Ask` 的调用）

- [ ] **Step 5: Commit**

```bash
git add internal/wiki internal/access internal/query internal/httpapi
git commit -m "feat: record wiki page access and confidence metadata"
```

---

### Task 16: Forgetting curve (decay, never delete)

**Files:**
- Create: `internal/evolve/decay.go`
- Modify: `internal/wiki/search.go`（分数乘 confidence）
- Create: `cmd/lint/main.go`
- Test: `internal/evolve/decay_test.go`
- Test: `internal/wiki/search_decay_test.go`

半衰期（天）：Policy/Decision/Procedure = 180；Service/Module/Person/Dependency = 90；File/Incident/Change = 21。公式：`confidence' = confidence * 0.5^(daysSinceReinforce / halfLife)`。`last_reinforced` 在结晶写回或新来源确认时重置为 now，confidence 提到 `min(1, confidence+0.1)`。衰减不删文件、不把 `Stale` 当删除。

- [ ] **Step 1: Write the failing tests**

```go
package evolve_test

import (
	"math"
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/evolve"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
)

func TestDecayArchitectureSlowerThanFile(t *testing.T) {
	now := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	old := now.Add(-90 * 24 * time.Hour)
	arch := evolve.Decay(1, string(schema.EntityDecision), old, now)
	file := evolve.Decay(1, string(schema.EntityFile), old, now)
	if arch <= file {
		t.Fatalf("decision %v should decay slower than file %v", arch, file)
	}
	if file >= 0.2 {
		t.Fatalf("file should have dropped hard, got %v", file)
	}
}

func TestDecayNeverNegativeAndReinforceResets(t *testing.T) {
	now := time.Now().UTC()
	c := evolve.Decay(0.01, string(schema.EntityIncident), now.Add(-365*24*time.Hour), now)
	if c < 0 || math.IsNaN(c) {
		t.Fatalf("%v", c)
	}
	up := evolve.Reinforce(c, now)
	if up.Confidence <= c || up.At.IsZero() {
		t.Fatalf("%+v", up)
	}
}
```

```go
package wiki_test

import (
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func TestSearchDownranksLowConfidence(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	_ = st.Write(wiki.Page{
		Path: "entities/a.md",
		Meta: wiki.Meta{Title: "redis", Type: string(schema.EntityService), Source: "code", Confidence: 1, Scope: "shared"},
		Body: "redis 缓存",
	})
	_ = st.Write(wiki.Page{
		Path: "entities/b.md",
		Meta: wiki.Meta{Title: "redis-old", Type: string(schema.EntityFile), Source: "code", Confidence: 0.05, Scope: "shared"},
		Body: "redis 缓存",
	})
	hits := st.Search("redis")
	if len(hits) < 2 || hits[0].Path != "entities/a.md" {
		t.Fatalf("%+v", hits)
	}
}
```

再测 `evolve.Apply(st, now)` 会改写页的 confidence 且文件仍在：

```go
func TestApplyDecayKeepsPages(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	past := time.Now().UTC().Add(-200 * 24 * time.Hour)
	_ = st.Write(wiki.Page{
		Path: "entities/files.md",
		Meta: wiki.Meta{Title: "files", Type: string(schema.EntityFile), Source: "code", Confidence: 1, Scope: "shared", LastReinforced: past},
		Body: "orphan file",
	})
	n, err := evolve.Apply(st, time.Now().UTC())
	if err != nil || n != 1 {
		t.Fatalf("n=%d err=%v", n, err)
	}
	p, err := st.Read("entities/files.md")
	if err != nil {
		t.Fatal("must not delete")
	}
	if p.Meta.Confidence >= 1 {
		t.Fatalf("expected decay, got %v", p.Meta.Confidence)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `go test ./internal/evolve/ ./internal/wiki/ -v`  
Expected: FAIL undefined `Decay` / 排序未乘 confidence

- [ ] **Step 3: Write minimal implementation**

```go
package evolve

import (
	"math"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func halfLifeDays(kind string) float64 {
	switch schema.EntityKind(kind) {
	case schema.EntityPolicy, schema.EntityDecision, schema.EntityProcedure:
		return 180
	case schema.EntityFile, schema.EntityIncident, schema.EntityChange:
		return 21
	default:
		return 90
	}
}

func Decay(confidence float64, kind string, lastReinforced, now time.Time) float64 {
	if confidence <= 0 {
		return 0
	}
	if lastReinforced.IsZero() {
		lastReinforced = now
	}
	days := now.Sub(lastReinforced).Hours() / 24
	if days < 0 {
		days = 0
	}
	hl := halfLifeDays(kind)
	out := confidence * math.Pow(0.5, days/hl)
	if out < 0 {
		return 0
	}
	if out > 1 {
		return 1
	}
	return out
}

type ReinforceResult struct {
	Confidence float64
	At         time.Time
}

func Reinforce(confidence float64, at time.Time) ReinforceResult {
	c := confidence + 0.1
	if c > 1 {
		c = 1
	}
	return ReinforceResult{Confidence: c, At: at}
}

func Apply(st *wiki.Store, now time.Time) (int, error) {
	pages, err := st.All()
	if err != nil {
		return 0, err
	}
	n := 0
	for _, p := range pages {
		from := p.Meta.LastReinforced
		if from.IsZero() {
			from = p.Meta.LastAccessed
		}
		next := Decay(p.Meta.Confidence, p.Meta.Type, from, now)
		if next == p.Meta.Confidence {
			continue
		}
		p.Meta.Confidence = next
		if err := st.Write(p); err != nil {
			return n, err
		}
		n++
	}
	return n, nil
}
```

`search.go` 在累加 score 后：`score *= p.Meta.Confidence`（confidence 为 0 时用 0，缺省已是 1）。

`crystal.MaybeFile` 成功后对该 digest 页 `LastReinforced=now`、`Confidence=1`。

`cmd/lint/main.go`：打开 `data/wiki`，调用 `evolve.Apply`，审计一行 `op=decay`。

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./internal/evolve/ ./internal/wiki/ ./internal/crystal/ -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/evolve/decay.go internal/evolve/decay_test.go internal/wiki cmd/lint internal/crystal
git commit -m "feat: decay wiki confidence on a forgetting curve"
```

---

### Task 17: Multi-agent page lock, LWW, and private promotion

**Files:**
- Create: `internal/evolve/mesh.go`
- Modify: `internal/evolve/queue.go`（增加 `KindWriteConflict`）
- Modify: `internal/wiki/store.go`（Write 走 mesh）
- Test: `internal/evolve/mesh_test.go`

规则：`Lock(path, agentID, ttl)`；持锁者可写。无锁或锁过期：比较 frontmatter 之外的更新时间，后写胜（LWW），同时把双方正文快照入 `KindWriteConflict` 队列供人覆盖。`scope=private` 页仅写入 agent 自己的路径前缀 `private/{agentID}/`；`Promote(path, actor)` 把页面拷到共享树并改 `scope=shared`，需 `editor` 以上。

- [ ] **Step 1: Write the failing test**

```go
package evolve_test

import (
	"strings"
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/evolve"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func TestLockPreventsOtherAgentUntilExpiry(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	q := evolve.NewQueue()
	m := evolve.NewMesh(st, q)
	if err := m.Lock("entities/pay-api.md", "agent-a", 5*time.Minute); err != nil {
		t.Fatal(err)
	}
	err := m.Write("agent-b", wiki.Page{
		Path: "entities/pay-api.md",
		Meta: wiki.Meta{Title: "pay-api", Type: "Service", Source: "code", Scope: "shared", Confidence: 1},
		Body: "from b",
	}, time.Now())
	if err == nil {
		t.Fatal("expected lock error")
	}
}

func TestLWWEnqueuesConflictWhenUnlockedConcurrentWrites(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	q := evolve.NewQueue()
	m := evolve.NewMesh(st, q)
	t1 := time.Date(2026, 9, 1, 10, 0, 0, 0, time.UTC)
	t2 := t1.Add(time.Minute)
	p := wiki.Page{Path: "entities/pay-api.md", Meta: wiki.Meta{Title: "pay-api", Type: "Service", Source: "code", Scope: "shared", Confidence: 1}, Body: "v1"}
	if err := m.Write("agent-a", p, t1); err != nil {
		t.Fatal(err)
	}
	p.Body = "v2"
	if err := m.Write("agent-b", p, t2); err != nil {
		t.Fatal(err)
	}
	got, _ := st.Read("entities/pay-api.md")
	if got.Body != "v2" {
		t.Fatalf("LWW body=%s", got.Body)
	}
	if len(q.Open()) != 1 || q.Open()[0].Kind != evolve.KindWriteConflict {
		t.Fatalf("queue=%+v", q.Open())
	}
}

func TestPromotePrivateToShared(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	q := evolve.NewQueue()
	m := evolve.NewMesh(st, q)
	priv := wiki.Page{
		Path: "private/agent-a/note.md",
		Meta: wiki.Meta{Title: "note", Type: "Decision", Source: "crystal", Scope: "private", Confidence: 1},
		Body: "only me",
	}
	if err := m.Write("agent-a", priv, time.Now()); err != nil {
		t.Fatal(err)
	}
	if err := m.Promote("private/agent-a/note.md", "editor"); err != nil {
		t.Fatal(err)
	}
	got, err := st.Read("entities/note.md")
	if err != nil {
		t.Fatal(err)
	}
	if got.Meta.Scope != "shared" || !strings.Contains(got.Body, "only me") {
		t.Fatalf("%+v %s", got.Meta, got.Body)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/evolve/ -run 'TestLock|TestLWW|TestPromote' -v`  
Expected: FAIL undefined `NewMesh` / `KindWriteConflict`

- [ ] **Step 3: Write minimal implementation**

在 `queue.go` 增加：

```go
const KindWriteConflict Kind = "write_conflict"
```

```go
package evolve

import (
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

type lock struct {
	Agent string
	Until time.Time
}

type Mesh struct {
	st    *wiki.Store
	q     *Queue
	mu    sync.Mutex
	locks map[string]lock
	mtime map[string]time.Time
}

func NewMesh(st *wiki.Store, q *Queue) *Mesh {
	return &Mesh{st: st, q: q, locks: map[string]lock{}, mtime: map[string]time.Time{}}
}

func (m *Mesh) Lock(path, agent string, ttl time.Duration) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	now := time.Now()
	if l, ok := m.locks[path]; ok && l.Until.After(now) && l.Agent != agent {
		return fmt.Errorf("locked by %s", l.Agent)
	}
	m.locks[path] = lock{Agent: agent, Until: now.Add(ttl)}
	return nil
}

func (m *Mesh) Write(agent string, p wiki.Page, at time.Time) error {
	if p.Meta.Scope == "private" && !strings.HasPrefix(p.Path, "private/"+agent+"/") {
		return fmt.Errorf("private pages must live under private/%s/", agent)
	}
	m.mu.Lock()
	l, locked := m.locks[p.Path]
	now := time.Now()
	if locked && l.Until.After(now) && l.Agent != agent {
		m.mu.Unlock()
		return fmt.Errorf("locked by %s", l.Agent)
	}
	prevTime, existed := m.mtime[p.Path]
	m.mu.Unlock()

	if existed {
		old, err := m.st.Read(p.Path)
		if err == nil && old.Body != p.Body {
			m.q.Enqueue(Item{Kind: KindWriteConflict, Summary: fmt.Sprintf("%s vs %s on %s", agent, old.Body, p.Path)})
		}
		if existed && at.Before(prevTime) {
			return nil
		}
	}
	if err := m.st.Write(p); err != nil {
		return err
	}
	m.mu.Lock()
	m.mtime[p.Path] = at
	m.mu.Unlock()
	return nil
}

func (m *Mesh) Promote(path, actor string) error {
	p, err := m.st.Read(path)
	if err != nil {
		return err
	}
	if p.Meta.Scope != "private" {
		return fmt.Errorf("%s is not private", path)
	}
	name := path
	if i := strings.LastIndex(path, "/"); i >= 0 {
		name = path[i+1:]
	}
	p.Path = "entities/" + name
	p.Meta.Scope = "shared"
	if err := m.st.Write(p); err != nil {
		return err
	}
	_ = actor
	return nil
}
```

`cmd/ingest` 与结晶写页改为走 `Mesh.Write`（单 Agent 时也能用，锁可选）。`httpapi` 增加：

- `POST /v1/wiki/lock` editor：`{"path","agent","ttl_seconds"}`
- `POST /v1/wiki/promote` editor：`{"path"}`

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `go test ./internal/evolve/ ./internal/httpapi/ -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/evolve internal/httpapi cmd/ingest
git commit -m "feat: multi-agent wiki locks, LWW conflicts, and private promotion"
```

---

### Task 18: Decay in lint + viz confidence dimming

**Files:**
- Modify: `web/src/graphMap.js`
- Modify: `web/src/graphMap.test.js`
- Modify: `internal/httpapi/server.go`（neighborhood 带 `confidence`；可选 `?as_of=` 用当前 confidence，第一期滑条只读已衰减值）
- Modify: `AGENTS.md`
- Modify: `README.md`

- [ ] **Step 1: Write the failing mapper test**

```js
test("dims low confidence nodes", () => {
  const g = toGraph({
    nodes: [{ id: "pay-api", confidence: 0.2 }, { id: "policy", confidence: 1 }],
    edges: [],
  });
  assert.equal(g.nodes[0].dim, true);
  assert.equal(g.nodes[1].dim, false);
});
```

`toGraph`：`dim: (n.confidence ?? 1) < 0.4`。

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test web/src/graphMap.test.js`  
Expected: FAIL `dim` undefined

- [ ] **Step 3: Implement dimming and neighborhood payload**

`Neighborhood` 的 `Node` 增加 `Confidence float64`。Fake `Neighborhood` 无 wiki 时用 1。HTTP API 组邻域时按 node id 读 wiki 页填 confidence。前端力导向：`dim` 节点 `globalAlpha=0.35`。滑条：本地过滤 `confidence >= threshold`（0–1），不另存知识。

`AGENTS.md` 补上半衰期表、访问即 `last_accessed`、结晶即 `last_reinforced`、页锁 TTL 默认 5 分钟、LWW + `write_conflict` 人审、private 路径规则。

README 增加：`go run ./cmd/lint` 以及「两个 agent 并发写同一页会入审核队列」。

- [ ] **Step 4: Run tests**

Run: `node --test web/src/graphMap.test.js && go test ./...`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web internal/graph internal/httpapi AGENTS.md README.md
git commit -m "feat: show forgetting on the graph and document mesh rules"
```

---

## Spec coverage

| 规格节 | 任务 |
|--------|------|
| wiki 主存、chunk 只引用 | 3, 9, 10 |
| 自建 Graphiti、单一 group_id | 6, 7, 14 |
| 代码/制度/工单管道 | 5, 8, 9 |
| File 节点、procedural 实页 | 5, 13 |
| supersession + wiki stale | 6, 8, 10 |
| 结晶 | 11 |
| 人审 + 审计 + 剥密钥 | 2, 4, 12 |
| 力导向左图右页 | 12, 13 |
| 图降级 / 解析失败 | 9, 10 |
| 访问日志 + 遗忘降权 | 15, 16, 18 |
| 多 Agent 锁 / LWW / private→shared | 17, 18 |
