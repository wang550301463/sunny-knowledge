package gateway

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

func testValkeyLimiter(t *testing.T, namespace string) *ValkeyLimiter {
	t.Helper()
	endpoint := os.Getenv("TEST_VALKEY_URL")
	if endpoint == "" {
		t.Skip("TEST_VALKEY_URL required for real Valkey acceptance")
	}
	limiter, err := NewValkeyLimiter(endpoint, namespace, 300*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(limiter.Close)
	if err = limiter.Ready(context.Background()); err != nil {
		t.Fatal("test Valkey unavailable")
	}
	return limiter
}
func TestSharedValkeyTwoGatewaysAtomicLimitAndExpiry(t *testing.T) {
	namespace := "test:" + platform.ID()
	a, b := testValkeyLimiter(t, namespace), testValkeyLimiter(t, namespace)
	var authCalls, targetCalls atomic.Int32
	authorizer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authCalls.Add(1)
		json.NewEncoder(w).Encode(platform.Resolved{Principal: platform.Principal{ID: "same-user"}})
	}))
	defer authorizer.Close()
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { targetCalls.Add(1); w.WriteHeader(204) }))
	defer target.Close()
	limits := RateLimits{Peer: WindowLimit{10, 750 * time.Millisecond}, IdentityPeer: WindowLimit{10, time.Minute}, Principal: WindowLimit{100, time.Minute}}
	handlers := make([]http.Handler, 2)
	for i, l := range []*ValkeyLimiter{a, b} {
		h, e := NewHandler(Config{AuthURL: authorizer.URL, Routes: map[string]string{"knowledge": target.URL}, Limiter: l, Limits: limits}, security(t))
		if e != nil {
			t.Fatal(e)
		}
		handlers[i] = h
	}
	var allowed, limited atomic.Int32
	var jobs sync.WaitGroup
	for i := 0; i < 40; i++ {
		jobs.Add(1)
		go func(i int) {
			defer jobs.Done()
			req := httptest.NewRequest("GET", "/api/v1/pages", nil)
			req.RemoteAddr = "192.0.2.1:" + strconv.Itoa(1000+i)
			req.Header.Set("X-Forwarded-For", strconv.Itoa(i))
			req.Header.Set("Forwarded", "for=fake")
			rec := httptest.NewRecorder()
			handlers[i%2].ServeHTTP(rec, req)
			switch rec.Code {
			case 204:
				allowed.Add(1)
			case 429:
				limited.Add(1)
				if rec.Header().Get("Retry-After") == "" {
					t.Error("no Retry-After")
				}
			default:
				t.Errorf("unexpected status %d", rec.Code)
			}
		}(i)
	}
	jobs.Wait()
	if allowed.Load() != 10 || limited.Load() != 30 || authCalls.Load() != 10 || targetCalls.Load() != 10 {
		t.Fatalf("allowed=%d limited=%d auth=%d target=%d", allowed.Load(), limited.Load(), authCalls.Load(), targetCalls.Load())
	}
	time.Sleep(850 * time.Millisecond)
	req := httptest.NewRequest("GET", "/api/v1/pages", nil)
	req.RemoteAddr = "192.0.2.1:22"
	rec := httptest.NewRecorder()
	handlers[0].ServeHTTP(rec, req)
	if rec.Code != 204 {
		t.Fatalf("window did not expire: %d", rec.Code)
	}
}
func TestPrincipalBucketIgnoresBearerAndPeerRotation(t *testing.T) {
	l := testValkeyLimiter(t, "test:"+platform.ID())
	auth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(platform.Resolved{Principal: platform.Principal{ID: "same-authoritative-user"}})
	}))
	defer auth.Close()
	var called atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called.Add(1); w.WriteHeader(204) }))
	defer target.Close()
	h, e := NewHandler(Config{AuthURL: auth.URL, Routes: map[string]string{"knowledge": target.URL}, Limiter: l, Limits: RateLimits{Peer: WindowLimit{100, time.Minute}, IdentityPeer: WindowLimit{100, time.Minute}, Principal: WindowLimit{2, time.Minute}}}, security(t))
	if e != nil {
		t.Fatal(e)
	}
	for i, want := range []int{204, 204, 429} {
		req := httptest.NewRequest("GET", "/api/v1/pages", nil)
		req.RemoteAddr = "192.0.2." + strconv.Itoa(i+1) + ":22"
		req.Header.Set("Authorization", "Bearer token-"+strconv.Itoa(i))
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		if rec.Code != want {
			t.Fatalf("attempt %d got %d want %d", i, rec.Code, want)
		}
	}
	if called.Load() != 2 {
		t.Fatal("rejected request reached target")
	}
}
func TestPeerLimitProtectsMCPAndIDPWithoutReplacingOAuth(t *testing.T) {
	l := testValkeyLimiter(t, "test:"+platform.ID())
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("WWW-Authenticate", `Bearer scope="knowledge:feedback"`)
		w.WriteHeader(401)
	}))
	defer target.Close()
	h, e := NewHandler(Config{KeycloakURL: target.URL, Routes: map[string]string{"mcp": target.URL}, Limiter: l, Limits: RateLimits{Peer: WindowLimit{1, time.Minute}, IdentityPeer: WindowLimit{1, time.Minute}, Principal: WindowLimit{1, time.Minute}}}, security(t))
	if e != nil {
		t.Fatal(e)
	}
	for _, path := range []string{"/mcp", "/idp/realms/knowledge/protocol/openid-connect/token"} {
		for _, want := range []int{401, 429} {
			rec := httptest.NewRecorder()
			req := httptest.NewRequest("POST", path, nil)
			req.RemoteAddr = "[::ffff:192.0.2.1]:20"
			h.ServeHTTP(rec, req)
			if rec.Code != want {
				t.Fatalf("%s got %d want %d", path, rec.Code, want)
			}
			if want == 401 && rec.Header().Get("WWW-Authenticate") != `Bearer scope="knowledge:feedback"` {
				t.Fatal("OAuth challenge changed")
			}
		}
	}
}
func TestLimiterFailureClosesDynamicRoutesButNotHealthAndStatic(t *testing.T) {
	l, e := NewValkeyLimiter("redis://127.0.0.1:1/0", "test:"+platform.ID(), 100*time.Millisecond)
	if e != nil {
		t.Fatal(e)
	}
	defer l.Close()
	web := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) }))
	defer web.Close()
	h, e := NewHandler(Config{WebURL: web.URL, KeycloakURL: web.URL, Routes: map[string]string{"knowledge": web.URL, "mcp": web.URL}, Limiter: l, Limits: DefaultRateLimits()}, security(t))
	if e != nil {
		t.Fatal(e)
	}
	for path, want := range map[string]int{"/healthz": 200, "/readyz": 503, "/assets/a.js": 204, "/api/v1/pages": 503, "/mcp": 503, "/idp/realms/a": 503} {
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest("GET", path, nil))
		if rec.Code != want {
			t.Fatalf("%s got %d want %d", path, rec.Code, want)
		}
	}
}
