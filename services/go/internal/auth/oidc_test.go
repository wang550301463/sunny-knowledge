package auth

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"github.com/golang-jwt/jwt/v5"
	"math/big"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestOIDCVerifiesDiscoveryJWKSAndClaims(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	var issuer string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/.well-known/openid-configuration":
			json.NewEncoder(w).Encode(map[string]string{"issuer": issuer, "jwks_uri": issuer + "/jwks"})
		case "/jwks":
			json.NewEncoder(w).Encode(map[string]any{"keys": []any{map[string]string{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "test", "n": base64.RawURLEncoding.EncodeToString(key.N.Bytes()), "e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(key.E)).Bytes())}}})
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	issuer = server.URL
	verifier := NewVerifier(issuer, issuer, "knowledge-web")
	base := func() jwt.MapClaims {
		return jwt.MapClaims{"iss": issuer, "sub": "alice", "aud": []string{"knowledge-web"}, "iat": time.Now().Unix(), "exp": time.Now().Add(time.Minute).Unix(), "scope": "openid knowledge:read knowledge:write", "preferred_username": "alice", "groups": []string{"stale-admin"}}
	}
	sign := func(c jwt.MapClaims) string {
		token := jwt.NewWithClaims(jwt.SigningMethodRS256, c)
		token.Header["kid"] = "test"
		s, err := token.SignedString(key)
		if err != nil {
			t.Fatal(err)
		}
		return "Bearer " + s
	}
	claims, err := verifier.Verify(context.Background(), sign(base()))
	if err != nil || claims.Subject != "alice" || !claims.HasScope("knowledge:read") {
		t.Fatalf("valid Keycloak token rejected: %+v %v", claims, err)
	}
	for _, tc := range []struct {
		name, key string
		value     any
	}{{"issuer", "iss", "wrong"}, {"audience", "aud", "wrong"}, {"expiry", "exp", time.Now().Add(-time.Minute).Unix()}, {"missing expiry", "exp", nil}, {"missing subject", "sub", ""}, {"future issuance", "iat", time.Now().Add(time.Minute).Unix()}} {
		t.Run(tc.name, func(t *testing.T) {
			c := base()
			c[tc.key] = tc.value
			if _, err := verifier.Verify(context.Background(), sign(c)); err == nil {
				t.Fatal("invalid claims accepted")
			}
		})
	}
	if _, err := verifier.Verify(context.Background(), "Bearer not-a-token"); err == nil {
		t.Fatal("malformed token accepted")
	}
}
