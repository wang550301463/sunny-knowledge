package query_test

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/access"
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
	ans, err := query.Ask(ctx, query.Request{Question: "升级 redis 会影响 pay-api 吗", Actor: "tester"}, st, g, cite, access.New())
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
