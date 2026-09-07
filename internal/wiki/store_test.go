package wiki_test

import (
	"path/filepath"
	"testing"
	"time"

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

func TestSearchDownranksLowConfidence(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	past := time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC)
	_ = st.Write(wiki.Page{
		Path: "entities/a.md",
		Meta: wiki.Meta{Title: "redis", Type: string(schema.EntityService), Source: "code", Confidence: 1, Scope: "shared"},
		Body: "redis 缓存",
	})
	_ = st.Write(wiki.Page{
		Path: "entities/b.md",
		Meta: wiki.Meta{Title: "redis-old", Type: string(schema.EntityFile), Source: "code", Confidence: 0.05, Scope: "shared", LastReinforced: past},
		Body: "redis 缓存",
	})
	hits := st.Search("redis")
	if len(hits) < 2 || hits[0].Path != "entities/a.md" {
		t.Fatalf("%+v", hits)
	}
}
