package audit_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/audit"
)

func TestLogAppendsJSONLine(t *testing.T) {
	dir := t.TempDir()
	l := audit.New(filepath.Join(dir, "audit.jsonl"))
	if err := l.Record("ingest", "editor", "wrote entities/pay-api.md"); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(filepath.Join(dir, "audit.jsonl"))
	if !strings.Contains(string(b), `"op":"ingest"`) {
		t.Fatalf("got %s", b)
	}
}
