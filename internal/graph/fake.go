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

func (f *Fake) Search(_ context.Context, query string) ([]Fact, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	q := strings.ToLower(query)
	parts := strings.Fields(q)
	var out []Fact
	for _, fact := range f.facts {
		hay := strings.ToLower(fact.Source + " " + fact.Target + " " + string(fact.Kind))
		if q == "" {
			out = append(out, fact)
			continue
		}
		if strings.Contains(hay, q) {
			out = append(out, fact)
			continue
		}
		for _, p := range parts {
			if p != "" && strings.Contains(hay, p) {
				out = append(out, fact)
				break
			}
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
		nodes[fact.Source] = Node{ID: fact.Source, Confidence: 1}
		nodes[fact.Target] = Node{ID: fact.Target, Confidence: 1}
	}
	nb := Neighborhood{Edges: edges}
	for _, n := range nodes {
		nb.Nodes = append(nb.Nodes, n)
	}
	return nb, nil
}
