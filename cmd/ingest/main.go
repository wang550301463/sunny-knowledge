package main

import (
	"context"
	"flag"
	"log"
	"os"

	"github.com/wang550301463/sunny-knowledge/internal/docs"
	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/ingest"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func main() {
	repo := flag.String("repo", "testdata/slice/repo", "code snapshot")
	policy := flag.String("policy", "testdata/slice/policies/refund.md", "policy file")
	ticket := flag.String("ticket", "testdata/slice/tickets/INC-1.json", "ticket json")
	wikiRoot := flag.String("wiki", "data/wiki", "wiki root")
	rawDir := flag.String("raw", "data/raw", "raw policy dir")
	flag.Parse()
	st := wiki.NewStore(*wikiRoot)
	ctx := context.Background()
	if err := ingest.CompileRepo(*repo, st); err != nil {
		log.Fatal(err)
	}
	if err := ingest.CompilePolicy(*policy, *rawDir, st, docs.NewFake()); err != nil {
		log.Fatal(err)
	}
	var g graph.Store = graph.NewFake()
	if u := os.Getenv("GRAPHITI_URL"); u != "" {
		g = graph.NewHTTP(u, schema.DefaultGroupID)
	}
	if err := ingest.ProjectWiki(ctx, st, g); err != nil {
		log.Printf("project wiki: %v", err)
	}
	raw, err := os.ReadFile(*ticket)
	if err != nil {
		log.Fatal(err)
	}
	_ = g.AddFact(ctx, graph.Fact{ID: "e-old", Source: "pay-api", Kind: schema.EdgeUses, Target: "redis-6"})
	if err := ingest.ApplyTicket(ctx, raw, st, g); err != nil {
		log.Fatal(err)
	}
	if f, ok := g.(*graph.Fake); ok {
		path := os.Getenv("GRAPH_PATH")
		if path == "" {
			path = "data/graph.json"
		}
		if err := graph.SaveFake(path, f); err != nil {
			log.Fatal(err)
		}
	}
	log.Printf("ingested into %s", *wikiRoot)
}
