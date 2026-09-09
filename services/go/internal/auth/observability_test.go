package auth

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
)

func TestJWKSHTTPClientTracesWithoutURLsOrCredentials(t *testing.T) {
	ex := tracetest.NewInMemoryExporter()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(ex))
	defer provider.Shutdown(context.Background())
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Traceparent") == "" {
			t.Error("JWKS trace context absent")
		}
		json.NewEncoder(w).Encode(map[string]any{"keys": []any{}})
	}))
	defer target.Close()
	verifier := NewVerifier(target.URL, target.URL, "knowledge-web")
	telemetry := platform.NewTelemetry("auth", provider)
	handler := telemetry.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var keys any
		if err := verifier.fetch(r.Context(), target.URL+"/PRIVATE-JWKS-ID?token=PRIVATE-QUERY", &keys); err != nil {
			t.Fatal(err)
		}
	}))
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", "/internal/v1/resolve", nil))
	spans := ex.GetSpans()
	if len(spans) != 2 {
		t.Fatalf("got %d spans want auth server and Keycloak client", len(spans))
	}
	raw, _ := json.Marshal(spans)
	for _, s := range []string{target.URL, "PRIVATE-JWKS-ID", "PRIVATE-QUERY"} {
		if strings.Contains(string(raw), s) {
			t.Fatal("private URL in trace")
		}
	}
}
