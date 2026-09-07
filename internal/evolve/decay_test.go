package evolve_test

import (
	"math"
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/evolve"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
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
