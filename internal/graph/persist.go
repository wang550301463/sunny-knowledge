package graph

import (
	"encoding/json"
	"os"
	"path/filepath"
)

func (f *Fake) Facts() []Fact {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]Fact, 0, len(f.facts))
	for _, fact := range f.facts {
		out = append(out, fact)
	}
	return out
}

func (f *Fake) Replace(facts []Fact) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.facts = map[string]Fact{}
	for _, fact := range facts {
		if fact.ID == "" {
			continue
		}
		f.facts[fact.ID] = fact
	}
}

func SaveFake(path string, f *Fake) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	b, err := json.MarshalIndent(f.Facts(), "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, b, 0o644)
}

func LoadFake(path string, f *Fake) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var facts []Fact
	if err := json.Unmarshal(b, &facts); err != nil {
		return err
	}
	f.Replace(facts)
	return nil
}
