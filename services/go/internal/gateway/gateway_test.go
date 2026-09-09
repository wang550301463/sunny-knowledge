package gateway

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func security(t *testing.T) *platform.ServiceSecurity {
	pub, key, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return platform.NewServiceSecurity(key, map[string]ed25519.PublicKey{"gateway": pub})
}
func TestGatewayRoutesAndStripsSpoofedIdentity(t *testing.T) {
	sec := security(t)
	authorizer := httptest.NewServer(sec.Middleware("auth", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer user-token" {
			w.WriteHeader(401)
			return
		}
		json.NewEncoder(w).Encode(platform.Resolved{Principal: platform.Principal{ID: "alice"}})
	})))
	defer authorizer.Close()
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if caller, err := sec.Verify(r.Header.Get("X-Service-Token"), "knowledge"); err != nil || caller != "gateway" {
			t.Errorf("invalid target token %s %v", caller, err)
		}
		for _, header := range []string{"X-Principal-ID", "X-User-ID", "X-Role", "X-Internal-Admin", "X-Service-Name"} {
			if r.Header.Get(header) != "" {
				t.Errorf("spoofed %s forwarded", header)
			}
		}
		if r.Header.Get("Authorization") != "Bearer user-token" {
			t.Error("user bearer lost")
		}
		if r.Header.Get("X-Forwarded-For") == "fake" {
			t.Error("forwarded spoof retained")
		}
		w.Write([]byte(r.URL.Path))
	}))
	defer target.Close()
	h, err := NewHandler(Config{AuthURL: authorizer.URL, Routes: map[string]string{"knowledge": target.URL}}, sec)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest("GET", "/api/v1/pages", nil)
	req.Header.Set("Authorization", "Bearer user-token")
	for _, header := range []string{"X-Principal-ID", "X-User-ID", "X-Role", "X-Internal-Admin", "X-Service-Name", "X-Forwarded-For", "X-Service-Token"} {
		req.Header.Set(header, "fake")
	}
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != 200 || rr.Body.String() != "/api/v1/pages" {
		t.Fatalf("route failed %d %s", rr.Code, rr.Body.String())
	}
}

func TestGatewayPreservesMCPResourceAndScopeChallenge(t *testing.T) {
	sec := security(t)
	authorizer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("MCP resource server must construct its own OAuth challenge")
		w.WriteHeader(401)
	}))
	defer authorizer.Close()
	challenge := `Bearer resource_metadata="https://knowledge.example/.well-known/oauth-protected-resource/mcp", error="insufficient_scope", scope="knowledge:feedback"`
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if caller, err := sec.Verify(r.Header.Get("X-Service-Token"), "mcp"); err != nil || caller != "gateway" {
			t.Error("missing gateway workload signature")
		}
		if r.Header.Get("X-Principal-ID") != "" {
			t.Error("forged principal forwarded")
		}
		if r.Header.Get("Authorization") != "Bearer original-token" {
			t.Error("original bearer lost")
		}
		if r.Header.Get("Mcp-Session-Id") != "session-1" || r.Header.Get("Last-Event-ID") != "7" {
			t.Error("MCP resume headers lost")
		}
		w.Header().Set("WWW-Authenticate", challenge)
		w.WriteHeader(403)
	}))
	defer target.Close()
	h, err := NewHandler(Config{AuthURL: authorizer.URL, Routes: map[string]string{"mcp": target.URL}}, sec)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{}`))
	req.Header.Set("Authorization", "Bearer original-token")
	req.Header.Set("X-Principal-ID", "forged-admin")
	req.Header.Set("Mcp-Session-Id", "session-1")
	req.Header.Set("Last-Event-ID", "7")
	response := httptest.NewRecorder()
	h.ServeHTTP(response, req)
	if response.Code != 403 || response.Header().Get("WWW-Authenticate") != challenge {
		t.Fatalf("MCP resource challenge replaced: %d %q", response.Code, response.Header().Get("WWW-Authenticate"))
	}
}
func TestGatewayStreamsBeforeCompletionAndCancelsUpstream(t *testing.T) {
	sec := security(t)
	authorizer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { json.NewEncoder(w).Encode(platform.Resolved{}) }))
	defer authorizer.Close()
	cancelled := make(chan struct{})
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: first\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
		close(cancelled)
	}))
	defer target.Close()
	h, err := NewHandler(Config{AuthURL: authorizer.URL, Routes: map[string]string{"agent": target.URL}}, sec)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(h)
	defer server.Close()
	ctx, cancel := context.WithCancel(context.Background())
	req, _ := http.NewRequestWithContext(ctx, "GET", server.URL+"/api/v1/runs/id/events", nil)
	req.Header.Set("Authorization", "Bearer user")
	resp, err := server.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 13)
	_, err = io.ReadFull(resp.Body, buf)
	if err != nil || !strings.Contains(string(buf), "first") {
		t.Fatalf("stream not flushed %q %v", buf, err)
	}
	cancel()
	resp.Body.Close()
	select {
	case <-cancelled:
	case <-time.After(time.Second):
		t.Fatal("downstream not cancelled")
	}
}
func TestGatewayRejectsUnroutedAPIAndAuthOutage(t *testing.T) {
	h, err := NewHandler(Config{AuthURL: "http://127.0.0.1:1", Routes: map[string]string{"knowledge": "http://127.0.0.1:1"}}, security(t))
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		path string
		want int
	}{{"/api/v1/pages", 503}, {"/api/v1/unknown", 404}} {
		req := httptest.NewRequest("GET", tc.path, nil)
		rr := httptest.NewRecorder()
		h.ServeHTTP(rr, req)
		if rr.Code != tc.want {
			t.Fatalf("%s status=%d want=%d", tc.path, rr.Code, tc.want)
		}
	}
}
