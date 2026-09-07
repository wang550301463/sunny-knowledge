package ingest_test

import (
	"context"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/ingest"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func sliceRepo(t *testing.T) string {
	t.Helper()
	_, file, _, _ := runtime.Caller(0)
	return filepath.Clean(filepath.Join(filepath.Dir(file), "..", "..", "testdata", "slice", "repo"))
}

func TestCompileRepoWritesServiceFilesAndProcedure(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	if err := ingest.CompileRepo(sliceRepo(t), st); err != nil {
		t.Fatal(err)
	}
	svc, err := st.Read("entities/pay-api.md")
	if err != nil {
		t.Fatal(err)
	}
	if svc.Meta.Type != string(schema.EntityService) {
		t.Fatalf("type=%s", svc.Meta.Type)
	}
	files, err := st.Read("entities/files.md")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(files.Body, "internal/refund/refund.go") {
		t.Fatalf("missing file node listing: %s", files.Body)
	}
	proc, err := st.Read("procedures/change.md")
	if err != nil {
		t.Fatal(err)
	}
	if proc.Meta.Type != string(schema.EntityProcedure) {
		t.Fatalf("proc type=%s", proc.Meta.Type)
	}
	g := graph.NewFake()
	if err := ingest.ProjectWiki(context.Background(), st, g); err != nil {
		t.Fatal(err)
	}
	nb, err := g.Neighborhood(context.Background(), "pay-api")
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, e := range nb.Edges {
		if e.Target == "internal/refund/refund.go" && e.Kind == schema.EdgeOwns {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected file ownership edge: %+v", nb.Edges)
	}
}
