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
