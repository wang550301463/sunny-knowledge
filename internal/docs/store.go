package docs

type Store interface {
	Parse(path string) (string, error)
	Cite(docID string) (string, error)
}
