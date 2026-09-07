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
