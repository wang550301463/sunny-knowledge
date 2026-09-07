package ingest

import (
	"os"
	"path/filepath"

	"github.com/wang550301463/sunny-knowledge/internal/docs"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/secret"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func CompilePolicy(src, rawDir string, st *wiki.Store, d docs.Store) error {
	text, err := d.Parse(src)
	dst := filepath.Join(rawDir, filepath.Base(src))
	orig, _ := os.ReadFile(src)
	_ = os.MkdirAll(rawDir, 0o755)
	_ = os.WriteFile(dst, orig, 0o644)
	if err != nil {
		return err
	}
	return st.Write(wiki.Page{
		Path: "entities/policy-refund.md",
		Meta: wiki.Meta{Title: "退款制度", Type: string(schema.EntityPolicy), Source: "policy", Confidence: 1, Scope: "shared"},
		Body: secret.Strip(text),
	})
}
