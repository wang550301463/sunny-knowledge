package auth

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

func TestResolveReturnsOnlyVerifiedTokenMetadataAndLivePrincipal(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	pub, serviceKey, _ := ed25519.GenerateKey(rand.Reader)
	sec := platform.NewServiceSecurity(serviceKey, map[string]ed25519.PublicKey{"mcp": pub, "auth": pub})
	iamCalls := 0
	iam := httptest.NewServer(sec.Middleware("iam", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		iamCalls++
		var input map[string]string
		if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
			t.Fatal(err)
		}
		if input["id"] != "alice" || input["azp"] != "knowledge-mcp" {
			t.Errorf("verified identity was changed")
		}
		platform.JSON(w, 200, platform.Principal{ID: "alice", Subjects: []string{"user:alice"}, AuthEpoch: 17})
	})))
	defer iam.Close()
	verifier := NewVerifier("https://idp.example/realm", "", "knowledge-api")
	verifier.keys = map[string]*rsa.PublicKey{"token-key": &key.PublicKey}
	verifier.loaded = time.Now()
	handler := NewHandler(verifier, platform.NewClient("auth", sec), iam.URL, sec)
	expiry := time.Now().Add(time.Minute).Unix()
	base := jwt.MapClaims{"iss": verifier.Issuer, "sub": "alice", "aud": []string{"knowledge-api", "https://knowledge.example/mcp"}, "azp": "knowledge-mcp", "exp": expiry, "iat": time.Now().Unix(), "scope": "knowledge:read", "groups": []string{"stale-private-group"}}
	for _, tc := range []struct {
		name, field string
		value       any
		want        int
	}{
		{"valid", "", nil, 200},
		{"wrong audience", "aud", []string{"https://knowledge.example/mcp"}, 401},
		{"wrong issuer", "iss", "https://attacker.example", 401},
		{"expired", "exp", time.Now().Add(-time.Minute).Unix(), 401},
	} {
		t.Run(tc.name, func(t *testing.T) {
			claims := jwt.MapClaims{}
			for k, v := range base {
				claims[k] = v
			}
			if tc.field != "" {
				claims[tc.field] = tc.value
			}
			token := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
			token.Header["kid"] = "token-key"
			bearer, err := token.SignedString(key)
			if err != nil {
				t.Fatal(err)
			}
			request := httptest.NewRequest("POST", "/internal/v1/resolve", bytes.NewBufferString(`{}`))
			workload, _ := sec.Mint("mcp", "auth")
			request.Header.Set("X-Service-Token", workload)
			request.Header.Set("Authorization", "Bearer "+bearer)
			request.Header.Set("X-Principal-Audience", "forged-resource")
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != tc.want {
				t.Fatalf("status %d, want %d", response.Code, tc.want)
			}
			if tc.want != 200 {
				return
			}
			var result map[string]any
			if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(result["audiences"], []any{"knowledge-api", "https://knowledge.example/mcp"}) || result["issuer"] != verifier.Issuer || result["client_id"] != "knowledge-mcp" || result["expires_at"] != float64(expiry) {
				t.Fatalf("verified metadata absent or altered: %v", result)
			}
			principal := result["principal"].(map[string]any)
			if !reflect.DeepEqual(principal["subjects"], []any{"user:alice"}) || principal["auth_epoch"] != float64(17) {
				t.Fatal("stale JWT memberships replaced IAM state")
			}
		})
	}
	if iamCalls != 1 {
		t.Fatalf("invalid token resolved against IAM: %d calls", iamCalls)
	}
}
