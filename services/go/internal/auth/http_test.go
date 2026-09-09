package auth

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"net/http/httptest"
	"testing"
)

func TestAuthHTTPRejectsPublicPrincipalAndUnsignedRequests(t *testing.T) {
	pub, key, _ := ed25519.GenerateKey(rand.Reader)
	sec := platform.NewServiceSecurity(key, map[string]ed25519.PublicKey{"gateway": pub})
	h := NewHandler(NewVerifier("", "", ""), platform.NewClient("auth", sec), "http://127.0.0.1:1", sec)
	for _, tc := range []struct {
		body   string
		signed bool
		want   int
	}{{`{}`, false, 401}, {`{"principal_id":"admin","action":"read","space_id":"s"}`, true, 400}, {`{"action":"read","space_id":"s"}`, true, 401}} {
		req := httptest.NewRequest("POST", "/internal/v1/authorize", bytes.NewBufferString(tc.body))
		if tc.signed {
			token, _ := sec.Mint("gateway", "auth")
			req.Header.Set("X-Service-Token", token)
		}
		rr := httptest.NewRecorder()
		h.ServeHTTP(rr, req)
		if rr.Code != tc.want {
			t.Fatalf("status=%d want=%d body=%s", rr.Code, tc.want, rr.Body.String())
		}
	}
}
