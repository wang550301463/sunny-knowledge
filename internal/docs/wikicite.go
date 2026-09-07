package docs

import (
	"os"

	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

// WikiCite parses local files and cites compiled wiki clause pages.
type WikiCite struct {
	Wiki *wiki.Store
}

func NewWikiCite(w *wiki.Store) *WikiCite { return &WikiCite{Wiki: w} }

func (w *WikiCite) Parse(path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return string(b), nil
}

func (w *WikiCite) Cite(docID string) (string, error) {
	p, err := w.Wiki.Read("entities/" + docID + ".md")
	if err != nil {
		return "", err
	}
	return p.Body, nil
}
