package gateway

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
)

func TestGatewayProxyTracesAndPreservesSSEContextLifetime(t *testing.T) {
	ex := tracetest.NewInMemoryExporter()
	p := sdktrace.NewTracerProvider(sdktrace.WithSyncer(ex))
	defer p.Shutdown(context.Background())
	telemetry := platform.NewTelemetry("gateway", p)
	auth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(platform.Resolved{Principal: platform.Principal{ID: "user"}})
	}))
	defer auth.Close()
	cancelled := make(chan struct{})
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Traceparent") == "" || r.Header.Get("Baggage") != "" || r.Header.Get("Tracestate") != "" {
			t.Error("unsafe/missing proxy trace carrier")
		}
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: first\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
		close(cancelled)
	}))
	defer target.Close()
	h, e := NewHandler(Config{AuthURL: auth.URL, Routes: map[string]string{"agent": target.URL}}, security(t))
	if e != nil {
		t.Fatal(e)
	}
	server := httptest.NewServer(telemetry.Middleware(h))
	defer server.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, "GET", server.URL+"/api/v1/runs/PRIVATE-RUN/events?token=PRIVATE-QUERY", nil)
	req.Header.Set("Authorization", "Bearer PRIVATE-TOKEN")
	req.Header.Set("Baggage", "secret=PRIVATE-BAG")
	req.Header.Set("Tracestate", "vendor=PRIVATE-STATE")
	response, e := server.Client().Do(req)
	if e != nil {
		t.Fatal(e)
	}
	buf := make([]byte, 13)
	if _, e = io.ReadFull(response.Body, buf); e != nil {
		t.Fatal(e)
	}
	if len(ex.GetSpans()) != 1 {
		t.Fatal("proxy/server span ended before stream completion")
	}
	cancel()
	response.Body.Close()
	select {
	case <-cancelled:
	case <-time.After(time.Second):
		t.Fatal("upstream cancellation lost")
	}
	deadline := time.Now().Add(time.Second)
	for len(ex.GetSpans()) < 3 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	spans := ex.GetSpans()
	if len(spans) != 3 {
		t.Fatalf("got %d spans want gateway+auth client+proxy client", len(spans))
	}
	var root trace.SpanID
	for _, s := range spans {
		if s.SpanKind == trace.SpanKindServer {
			root = s.SpanContext.SpanID()
		}
	}
	for _, s := range spans {
		if s.SpanKind == trace.SpanKindClient && s.Parent.SpanID() != root {
			t.Fatal("proxy/client disconnected from gateway root")
		}
	}
	raw, _ := json.Marshal(spans)
	if strings.Contains(string(raw), "PRIVATE-") {
		t.Fatal("private proxy input in telemetry")
	}
}
