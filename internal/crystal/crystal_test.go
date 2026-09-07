package crystal_test

import (
	"context"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/crystal"
	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/query"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func TestCrystallizeWritesDigestWhenQualityHigh(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	g := graph.NewFake()
	ans := query.Answer{Text: "pay-api 现用 redis-7", Pages: []string{"summaries/INC-1.md"}}
	if err := crystal.MaybeFile(context.Background(), ans, 0.9, st, g); err != nil {
		t.Fatal(err)
	}
	p, err := st.Read("summaries/digest-latest.md")
	if err != nil {
		t.Fatal(err)
	}
	if p.Meta.Source != "crystal" {
		t.Fatalf("%s", p.Meta.Source)
	}
}

func TestCrystallizeSkipsLowQuality(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	g := graph.NewFake()
	ans := query.Answer{Text: "不知道"}
	_ = crystal.MaybeFile(context.Background(), ans, 0.1, st, g)
	if _, err := st.Read("summaries/digest-latest.md"); err == nil {
		t.Fatal("should not write")
	}
}
