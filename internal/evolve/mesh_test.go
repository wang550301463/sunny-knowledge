package evolve_test

import (
	"strings"
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/evolve"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func TestLockPreventsOtherAgentUntilExpiry(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	q := evolve.NewQueue()
	m := evolve.NewMesh(st, q)
	if err := m.Lock("entities/pay-api.md", "agent-a", 5*time.Minute); err != nil {
		t.Fatal(err)
	}
	err := m.Write("agent-b", wiki.Page{
		Path: "entities/pay-api.md",
		Meta: wiki.Meta{Title: "pay-api", Type: "Service", Source: "code", Scope: "shared", Confidence: 1},
		Body: "from b",
	}, time.Now())
	if err == nil {
		t.Fatal("expected lock error")
	}
}

func TestLWWEnqueuesConflictWhenUnlockedConcurrentWrites(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	q := evolve.NewQueue()
	m := evolve.NewMesh(st, q)
	t1 := time.Date(2026, 9, 1, 10, 0, 0, 0, time.UTC)
	t2 := t1.Add(time.Minute)
	p := wiki.Page{Path: "entities/pay-api.md", Meta: wiki.Meta{Title: "pay-api", Type: "Service", Source: "code", Scope: "shared", Confidence: 1}, Body: "v1"}
	if err := m.Write("agent-a", p, t1); err != nil {
		t.Fatal(err)
	}
	p.Body = "v2"
	if err := m.Write("agent-b", p, t2); err != nil {
		t.Fatal(err)
	}
	got, _ := st.Read("entities/pay-api.md")
	if got.Body != "v2" {
		t.Fatalf("LWW body=%s", got.Body)
	}
	if len(q.Open()) != 1 || q.Open()[0].Kind != evolve.KindWriteConflict {
		t.Fatalf("queue=%+v", q.Open())
	}
}

func TestPromotePrivateToShared(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	q := evolve.NewQueue()
	m := evolve.NewMesh(st, q)
	priv := wiki.Page{
		Path: "private/agent-a/note.md",
		Meta: wiki.Meta{Title: "note", Type: "Decision", Source: "crystal", Scope: "private", Confidence: 1},
		Body: "only me",
	}
	if err := m.Write("agent-a", priv, time.Now()); err != nil {
		t.Fatal(err)
	}
	if err := m.Promote("private/agent-a/note.md", "editor"); err != nil {
		t.Fatal(err)
	}
	got, err := st.Read("entities/note.md")
	if err != nil {
		t.Fatal(err)
	}
	if got.Meta.Scope != "shared" || !strings.Contains(got.Body, "only me") {
		t.Fatalf("%+v %s", got.Meta, got.Body)
	}
}
