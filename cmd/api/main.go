package main

import (
	"log"
	"net/http"
	"os"

	"github.com/wang550301463/sunny-knowledge/internal/access"
	"github.com/wang550301463/sunny-knowledge/internal/docs"
	"github.com/wang550301463/sunny-knowledge/internal/evolve"
	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/httpapi"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func main() {
	root := env("WIKI_ROOT", "data/wiki")
	st := wiki.NewStore(root)
	var g graph.Store = graph.NewFake()
	if u := os.Getenv("GRAPHITI_URL"); u != "" {
		g = graph.NewHTTP(u, schema.DefaultGroupID)
	} else if f, ok := g.(*graph.Fake); ok {
		path := env("GRAPH_PATH", "data/graph.json")
		if err := graph.LoadFake(path, f); err != nil {
			log.Printf("graph snapshot %s: %v (empty graph until ingest)", path, err)
		}
	}
	token := os.Getenv("SUNNY_TOKEN")
	if token == "" {
		log.Fatal("SUNNY_TOKEN required")
	}
	var cite docs.Store = docs.NewWikiCite(st)
	if u := os.Getenv("RAGFLOW_URL"); u != "" {
		cite = docs.NewRAGFlow(u, os.Getenv("RAGFLOW_API_KEY"))
	}
	q := evolve.NewQueue()
	h := httpapi.New(httpapi.Deps{
		Wiki: st, Graph: g, Cite: cite, Reviews: q,
		Mesh: evolve.NewMesh(st, q), Access: access.New(), Token: token,
	})
	addr := env("HTTP_ADDR", ":8080")
	log.Printf("sunny-knowledge api on %s", addr)
	log.Fatal(http.ListenAndServe(addr, h))
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
