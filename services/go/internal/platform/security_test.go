package platform

import (
	"crypto/ed25519"
	"crypto/rand"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// testSecurity builds a workload identity for tests. Every service shares one
// public key so fixtures only exercise identity, audience and lifetime rules.
func testSecurity(t *testing.T) *ServiceSecurity {
	t.Helper()
	_, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return NewServiceSecurity(private, testPublicRegistry(t))
}

func testPublicRegistry(t *testing.T) map[string]ed25519.PublicKey {
	t.Helper()
	public, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	registry := map[string]ed25519.PublicKey{}
	for _, service := range []string{"auth", "channel", "agent", "knowledge", "retrieval", "graphiti", "llm", "iam", "gateway", "mcp"} {
		registry[service] = public
	}
	return registry
}

func TestServiceTokenEnforcesAudienceAndLifetime(t *testing.T) {
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	registry := map[string]ed25519.PublicKey{"gateway": public, "auth": public, "iam": public}
	sec := NewServiceSecurity(private, registry)
	token, err := sec.Mint("gateway", "auth")
	if err != nil {
		t.Fatal(err)
	}
	if caller, err := sec.Verify(token, "auth"); err != nil || caller != "gateway" {
		t.Fatalf("valid token rejected: %s %v", caller, err)
	}
	if _, err := sec.Verify(token, "iam"); err == nil {
		t.Fatal("cross-service replay accepted")
	}
	for _, tc := range []struct {
		name   string
		claims jwt.RegisteredClaims
	}{
		{"long lifetime", jwt.RegisteredClaims{Issuer: "knowledge-services", Subject: "gateway", Audience: []string{"auth"}, IssuedAt: jwt.NewNumericDate(time.Now()), ExpiresAt: jwt.NewNumericDate(time.Now().Add(2 * time.Minute))}},
		{"no expiry", jwt.RegisteredClaims{Issuer: "knowledge-services", Subject: "gateway", Audience: []string{"auth"}}},
		{"expired", jwt.RegisteredClaims{Issuer: "knowledge-services", Subject: "gateway", Audience: []string{"auth"}, IssuedAt: jwt.NewNumericDate(time.Now().Add(-time.Minute)), ExpiresAt: jwt.NewNumericDate(time.Now().Add(-time.Second))}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tok, _ := jwt.NewWithClaims(jwt.SigningMethodEdDSA, tc.claims).SignedString(private)
			if _, err := sec.Verify(tok, "auth"); err == nil {
				t.Fatal("unsafe token accepted")
			}
		})
	}
}

func TestServiceTokenRejectsUnknownWorkloadKey(t *testing.T) {
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	_, otherPrivate, _ := ed25519.GenerateKey(rand.Reader)
	sec := NewServiceSecurity(private, map[string]ed25519.PublicKey{"gateway": public})
	if _, err := sec.Mint("", "auth"); err == nil {
		t.Fatal("empty caller accepted")
	}
	if _, err := sec.Mint("gateway", ""); err == nil {
		t.Fatal("empty target accepted")
	}
	tok, _ := jwt.NewWithClaims(jwt.SigningMethodEdDSA, jwt.RegisteredClaims{Issuer: "knowledge-services", Subject: "intruder", Audience: []string{"auth"}, IssuedAt: jwt.NewNumericDate(time.Now()), ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Second))}).SignedString(otherPrivate)
	if _, err := sec.Verify(tok, "auth"); err == nil {
		t.Fatal("unknown workload key accepted")
	}
}

func TestServiceMiddlewareRejectsSpoofedHeader(t *testing.T) {
	h := testSecurity(t).Middleware("iam", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) }))
	req := httptest.NewRequest("GET", "/internal/v1/principals/admin", nil)
	req.Header.Set("X-Service-Name", "auth")
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != 401 {
		t.Fatalf("spoofed caller status %d", rr.Code)
	}
}
