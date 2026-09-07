package secret_test

import (
	"strings"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/secret"
)

func TestStripRedactsKeysAndTokens(t *testing.T) {
	in := "token=ghp_abcdefghijklmnopqrstuvwx password=supersecret openai=sk-abc123456789"
	out := secret.Strip(in)
	if strings.Contains(out, "ghp_") || strings.Contains(out, "supersecret") || strings.Contains(out, "sk-abc") {
		t.Fatalf("still leaked: %s", out)
	}
	if !strings.Contains(out, "[REDACTED]") {
		t.Fatal("expected redaction marker")
	}
}
