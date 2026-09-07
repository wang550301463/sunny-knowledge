package ingest_test

import (
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/docs"
	"github.com/wang550301463/sunny-knowledge/internal/ingest"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

type boomParse struct{}

func (boomParse) Parse(string) (string, error) { return "", errors.New("ragflow down") }

func (boomParse) Cite(string) (string, error) { return "", errors.New("unused") }

func TestPolicyCompileWritesWikiAndKeepsRawOnParseError(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	rawDir := t.TempDir()
	src := filepath.Join(t.TempDir(), "refund.md")
	_ = os.WriteFile(src, []byte("第3条 退款须在 7 日内完成。"), 0o644)
	cite := docs.NewFake()
	if err := ingest.CompilePolicy(src, rawDir, st, cite); err != nil {
		t.Fatal(err)
	}
	p, err := st.Read("entities/policy-refund.md")
	if err != nil {
		t.Fatal(err)
	}
	if p.Meta.Type != string(schema.EntityPolicy) {
		t.Fatalf("%s", p.Meta.Type)
	}
	got, err := cite.Cite("policy-refund")
	if err != nil || got == "" {
		t.Fatalf("cite=%q err=%v", got, err)
	}
	fail := wiki.NewStore(t.TempDir())
	if err := ingest.CompilePolicy(src, t.TempDir(), fail, boomParse{}); err == nil {
		t.Fatal("expected parse error")
	}
	if _, err := fail.Read("entities/policy-refund.md"); err == nil {
		t.Fatal("must not write fake policy page")
	}
}
