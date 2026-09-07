package access_test

import (
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/access"
)

func TestRecordAndLastAccess(t *testing.T) {
	l := access.New()
	at := time.Date(2026, 9, 1, 12, 0, 0, 0, time.UTC)
	l.Record("entities/pay-api.md", "editor", at)
	got, ok := l.Last("entities/pay-api.md")
	if !ok || !got.Equal(at) {
		t.Fatalf("got %v ok=%v", got, ok)
	}
}
