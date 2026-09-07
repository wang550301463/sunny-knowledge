package schema_test

import (
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/schema"
)

func TestEntityKindsIncludeFileAndProcedure(t *testing.T) {
	if !schema.IsEntityKind("File") {
		t.Fatal("File must be a first-class entity")
	}
	if !schema.IsEntityKind("Procedure") {
		t.Fatal("Procedure must be a first-class entity")
	}
	if schema.IsEntityKind("Chunk") {
		t.Fatal("Chunk is not an entity")
	}
}

func TestEdgeKindsIncludeSupersedes(t *testing.T) {
	if !schema.IsEdgeKind("supersedes") {
		t.Fatal("supersedes required")
	}
}
