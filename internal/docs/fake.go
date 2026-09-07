package docs

import (
	"fmt"
	"os"
)

type Fake struct {
	parsed map[string]string
}

func NewFake() *Fake { return &Fake{parsed: map[string]string{}} }

func (f *Fake) Parse(path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	f.parsed["policy-refund"] = string(b)
	return string(b), nil
}

func (f *Fake) Cite(docID string) (string, error) {
	s, ok := f.parsed[docID]
	if !ok {
		return "", fmt.Errorf("no citation for %s", docID)
	}
	return s, nil
}
