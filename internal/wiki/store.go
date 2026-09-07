package wiki

import (
	"os"
	"path/filepath"
	"strings"
)

type Store struct {
	Root string
}

func NewStore(root string) *Store { return &Store{Root: root} }

func (s *Store) abs(rel string) (string, error) {
	rel = filepath.ToSlash(filepath.Clean(rel))
	if strings.HasPrefix(rel, "..") {
		return "", os.ErrInvalid
	}
	return filepath.Join(s.Root, filepath.FromSlash(rel)), nil
}

func (s *Store) Write(p Page) error {
	path, err := s.abs(p.Path)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(p.Render()), 0o644)
}

func (s *Store) Read(rel string) (Page, error) {
	path, err := s.abs(rel)
	if err != nil {
		return Page{}, err
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return Page{}, err
	}
	return Parse(filepath.ToSlash(rel), string(b))
}

func (s *Store) All() ([]Page, error) {
	var pages []Page
	err := filepath.WalkDir(s.Root, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.HasSuffix(path, ".md") {
			return err
		}
		rel, err := filepath.Rel(s.Root, path)
		if err != nil {
			return err
		}
		p, err := s.Read(filepath.ToSlash(rel))
		if err != nil {
			return err
		}
		pages = append(pages, p)
		return nil
	})
	return pages, err
}
