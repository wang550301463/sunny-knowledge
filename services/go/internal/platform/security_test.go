package platform

import (
	"github.com/golang-jwt/jwt/v5"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

const testSecret = "0123456789abcdef0123456789abcdef"

func TestServiceTokenEnforcesAudienceAndLifetime(t *testing.T) {
	sec := NewServiceSecurity(testSecret)
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
			tok, _ := jwt.NewWithClaims(jwt.SigningMethodHS256, tc.claims).SignedString([]byte(testSecret))
			if _, err := sec.Verify(tok, "auth"); err == nil {
				t.Fatal("unsafe token accepted")
			}
		})
	}
}
func TestServiceMiddlewareRejectsSpoofedHeader(t *testing.T) {
	h := NewServiceSecurity(testSecret).Middleware("iam", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) }))
	req := httptest.NewRequest("GET", "/internal/v1/principals/admin", nil)
	req.Header.Set("X-Service-Name", "auth")
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != 401 {
		t.Fatalf("spoofed caller status %d", rr.Code)
	}
}
