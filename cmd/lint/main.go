package main

import (
	"log"
	"os"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/audit"
	"github.com/wang550301463/sunny-knowledge/internal/evolve"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func main() {
	root := "data/wiki"
	if v := os.Getenv("WIKI_ROOT"); v != "" {
		root = v
	}
	st := wiki.NewStore(root)
	n, err := evolve.Apply(st, time.Now().UTC())
	if err != nil {
		log.Fatal(err)
	}
	l := audit.New("data/audit.jsonl")
	_ = l.Record("decay", "lint", "updated pages")
	log.Printf("decayed %d pages", n)
}
