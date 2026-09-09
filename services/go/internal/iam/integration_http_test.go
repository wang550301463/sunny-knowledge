package iam

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"github.com/golang-jwt/jwt/v5"
	authsvc "github.com/wang550301463/sunny-knowledge/services/go/internal/auth"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/gateway"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"math/big"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestPostgresHTTPTrustChainRevocationAndScopes(t *testing.T) {
	store := integrationStore(t)
	public := map[string]ed25519.PublicKey{}
	private := map[string]ed25519.PrivateKey{}
	for _, service := range []string{"iam", "auth", "gateway"} {
		pub, key, _ := ed25519.GenerateKey(rand.Reader)
		public[service] = pub
		private[service] = key
	}
	sec := func(name string) *platform.ServiceSecurity { return platform.NewServiceSecurity(private[name], public) }
	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	issuer := ""
	oidc := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/.well-known/openid-configuration" {
			platform.JSON(w, 200, map[string]string{"issuer": issuer, "jwks_uri": issuer + "/jwks"})
			return
		}
		platform.JSON(w, 200, map[string]any{"keys": []any{map[string]string{"kty": "RSA", "alg": "RS256", "kid": "key", "n": base64.RawURLEncoding.EncodeToString(key.N.Bytes()), "e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(key.E)).Bytes())}}})
	}))
	defer oidc.Close()
	issuer = oidc.URL
	userToken := func(id, scope string) string {
		token := jwt.NewWithClaims(jwt.SigningMethodRS256, jwt.MapClaims{"iss": issuer, "aud": "knowledge-web", "sub": id, "exp": time.Now().Add(time.Minute).Unix(), "iat": time.Now().Unix(), "scope": scope, "preferred_username": id, "groups": []string{"admin"}, "realm_access": map[string]any{"roles": []string{"platform_admin"}}})
		token.Header["kid"] = "key"
		raw, _ := token.SignedString(key)
		return "Bearer " + raw
	}
	iamServer := httptest.NewUnstartedServer(nil)
	iamURL := "http://" + iamServer.Listener.Addr().String()
	authServer := httptest.NewServer(authsvc.NewHandler(authsvc.NewVerifier(issuer, issuer, "knowledge-web"), platform.NewClient("auth", sec("auth")), iamURL, sec("auth")))
	defer authServer.Close()
	iamServer.Config.Handler = NewHandler(store, platform.NewClient("iam", sec("iam")), authServer.URL, sec("iam"))
	iamServer.Start()
	defer iamServer.Close()
	gw, err := gateway.NewHandler(gateway.Config{AuthURL: authServer.URL, Routes: map[string]string{"iam": iamURL}}, sec("gateway"))
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(gw)
	defer server.Close()
	call := func(method, path, bearer string, body any, want int) map[string]any {
		t.Helper()
		raw, _ := json.Marshal(body)
		req, _ := http.NewRequest(method, server.URL+path, bytes.NewReader(raw))
		req.Header.Set("Authorization", bearer)
		req.Header.Set("X-Principal-ID", "admin")
		req.Header.Set("X-Role", "platform_admin")
		resp, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		var out map[string]any
		_ = json.NewDecoder(resp.Body).Decode(&out)
		if resp.StatusCode != want {
			t.Fatalf("%s %s status=%d want=%d out=%+v", method, path, resp.StatusCode, want, out)
		}
		return out
	}
	admin := userToken("admin", "knowledge:read knowledge:write")
	alice := userToken("alice", "knowledge:read knowledge:write")
	call("GET", "/api/v1/me", alice, nil, 200)
	call("POST", "/api/v1/spaces", alice, map[string]string{"name": "Unauthorized"}, 403)
	call("POST", "/api/v1/spaces", userToken("admin", "knowledge:read"), map[string]string{"name": "No write scope"}, 403)
	created := call("POST", "/api/v1/spaces", admin, map[string]string{"name": "Private"}, 201)
	space := created["id"].(string)
	group := call("POST", "/api/v1/groups", admin, map[string]string{"name": "Dev"}, 201)
	groupID := group["id"].(string)
	call("PUT", "/api/v1/groups/"+groupID+"/members/alice", admin, nil, 204)
	call("PUT", "/api/v1/grants", admin, map[string]any{"space_id": space, "action": "read", "subjects": []string{"group:" + groupID}}, 200)
	if out := call("GET", "/api/v1/spaces", alice, nil, 200); len(out["items"].([]any)) != 1 {
		t.Fatal("member lost directory access")
	}
	if out := call("GET", "/api/v1/spaces", admin, nil, 200); len(out["items"].([]any)) != 0 {
		t.Fatal("admin implicit read")
	}
	call("DELETE", "/api/v1/groups/"+groupID+"/members/alice", admin, nil, 204)
	if out := call("GET", "/api/v1/spaces", alice, nil, 200); len(out["items"].([]any)) != 0 {
		t.Fatal("live group revocation failed")
	}
	call("PUT", "/api/v1/users/alice", admin, map[string]any{"name": "Alice", "active": false}, 204)
	call("GET", "/api/v1/me", alice, nil, 403)
}
