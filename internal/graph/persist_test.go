package graph_test

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
)

func TestSaveLoadFakeRoundTrip(t *testing.T) {
	ctx := context.Background()
	g := graph.NewFake()
	_ = g.AddFact(ctx, graph.Fact{ID: "owns-internal/refund/refund.go", Source: "pay-api", Kind: schema.EdgeOwns, Target: "internal/refund/refund.go"})
	path := filepath.Join(t.TempDir(), "graph.json")
	if err := graph.SaveFake(path, g); err != nil {
		t.Fatal(err)
	}
	got := graph.NewFake()
	if err := graph.LoadFake(path, got); err != nil {
		t.Fatal(err)
	}
	nb, err := got.Neighborhood(ctx, "pay-api")
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, e := range nb.Edges {
		if e.Target == "internal/refund/refund.go" {
			found = true
		}
	}
	if !found {
		t.Fatalf("missing file node: %+v", nb.Edges)
	}
}
