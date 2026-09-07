package graph

import (
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/schema"
)

type Fact struct {
	ID        string
	Source    string
	Kind      schema.EdgeKind
	Target    string
	ValidAt   time.Time
	InvalidAt *time.Time
	Episode   string
}

type Node struct {
	ID         string
	Kind       schema.EntityKind
	Confidence float64
}

type Neighborhood struct {
	Nodes []Node
	Edges []Fact
}

type Episode struct {
	Name    string
	Body    string
	GroupID string
	Source  string
}
