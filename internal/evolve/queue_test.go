package evolve_test

import (
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/evolve"
)

func TestQueueRequiresAdminKinds(t *testing.T) {
	q := evolve.NewQueue()
	id := q.Enqueue(evolve.Item{Kind: evolve.KindMergeEntity, Summary: "merge pay-api aliases"})
	if id == "" {
		t.Fatal("empty id")
	}
	open := q.Open()
	if len(open) != 1 || open[0].Kind != evolve.KindMergeEntity {
		t.Fatalf("%+v", open)
	}
	if err := q.Resolve(id, true, "admin"); err != nil {
		t.Fatal(err)
	}
	if len(q.Open()) != 0 {
		t.Fatal("should be empty")
	}
}
