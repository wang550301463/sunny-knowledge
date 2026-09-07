package ingest

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/secret"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func CompileRepo(repo string, st *wiki.Store) error {
	mod, err := os.ReadFile(filepath.Join(repo, "go.mod"))
	if err != nil {
		return err
	}
	name := moduleName(string(mod))
	readme, _ := os.ReadFile(filepath.Join(repo, "README.md"))
	body := secret.Strip(fmt.Sprintf("# %s\n\n%s\n", name, strings.TrimSpace(string(readme))))
	if err := st.Write(wiki.Page{
		Path: "entities/" + name + ".md",
		Meta: wiki.Meta{Title: name, Type: string(schema.EntityService), Source: "code", Confidence: 1, Scope: "shared"},
		Body: body,
	}); err != nil {
		return err
	}
	var files []string
	err = filepath.WalkDir(repo, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		rel, _ := filepath.Rel(repo, path)
		if strings.HasPrefix(rel, ".git") {
			return nil
		}
		files = append(files, filepath.ToSlash(rel))
		return nil
	})
	if err != nil {
		return err
	}
	var b strings.Builder
	fmt.Fprintf(&b, "%s 拥有以下文件（File 节点）：\n\n", name)
	for _, f := range files {
		fmt.Fprintf(&b, "- %s owned_by %s\n", f, name)
	}
	if err := st.Write(wiki.Page{
		Path: "entities/files.md",
		Meta: wiki.Meta{Title: name + " files", Type: string(schema.EntityFile), Source: "code", Confidence: 1, Scope: "shared"},
		Body: b.String(),
	}); err != nil {
		return err
	}
	change, err := os.ReadFile(filepath.Join(repo, "docs", "CHANGE.md"))
	if err != nil {
		return fmt.Errorf("procedure page required: %w", err)
	}
	return st.Write(wiki.Page{
		Path: "procedures/change.md",
		Meta: wiki.Meta{Title: "变更流程", Type: string(schema.EntityProcedure), Source: "code", Confidence: 1, Scope: "shared"},
		Body: secret.Strip(string(change)),
	})
}

func moduleName(gomod string) string {
	for _, line := range strings.Split(gomod, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "module ") {
			mod := strings.TrimSpace(strings.TrimPrefix(line, "module "))
			if i := strings.LastIndex(mod, "/"); i >= 0 {
				return mod[i+1:]
			}
			return mod
		}
	}
	return "service"
}
