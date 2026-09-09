package iam

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestIAMInternalBoundaryUsesVerifiedCaller(t *testing.T) {
	pub, key, _ := ed25519.GenerateKey(rand.Reader)
	sec := platform.NewServiceSecurity(key, map[string]ed25519.PublicKey{"knowledge": pub, "auth": pub})
	h := NewHandler(nil, platform.NewClient("iam", sec), "http://127.0.0.1:1", sec)
	for _, tc := range []struct {
		caller, path, body string
		want               int
	}{{"", "/internal/v1/check", `{}`, 401}, {"knowledge", "/internal/v1/check", `{"principal_id":"admin","action":"read","space_id":"s"}`, 403}, {"knowledge", "/internal/v1/principals/ensure", `{"id":"admin"}`, 403}, {"auth", "/internal/v1/resources", `{"space_id":"s","resource_id":"r"}`, 403}, {"knowledge", "/api/v1/users", ``, 403}} {
		req := httptest.NewRequest("POST", tc.path, bytes.NewBufferString(tc.body))
		if tc.caller != "" {
			token, _ := sec.Mint(tc.caller, "iam")
			req.Header.Set("X-Service-Token", token)
		}
		req.Header.Set("X-Principal-ID", "admin")
		rr := httptest.NewRecorder()
		h.ServeHTTP(rr, req)
		if rr.Code != tc.want {
			t.Fatalf("%s %s: status=%d want=%d %s", tc.caller, tc.path, rr.Code, tc.want, rr.Body.String())
		}
	}
	_ = http.MethodGet
}
