package channel

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestPostgresHTTPRejectsForgedIdentityAndHidesSecret(t *testing.T) {
	s := integrationStore(t)
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	sec := platform.NewServiceSecurity(priv, map[string]ed25519.PublicKey{"gateway": pub, "channel": pub, "auth": pub})
	auth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer real-web-token" {
			w.WriteHeader(401)
			return
		}
		platform.JSON(w, 200, platform.Resolved{Principal: platform.Principal{ID: "admin", Permissions: []string{"platform_admin"}}, Scopes: []string{"knowledge:read", "knowledge:write"}})
	}))
	defer auth.Close()
	h := NewHandler(s, platform.NewClient("channel", sec), auth.URL, sec, testVerifier{})
	input := ConfigInput{Name: "bot", BotID: "bot", BotSecret: "never-echo-this", AgentID: "a", SpaceIDs: []string{"s"}}
	body, _ := json.Marshal(input)
	request := func(caller, bearer string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "/api/v1/channels", bytes.NewReader(body))
		token, _ := sec.Mint(caller, "channel")
		req.Header.Set("X-Service-Token", token)
		req.Header.Set("Authorization", bearer)
		req.Header.Set("X-User-ID", "admin")
		w := httptest.NewRecorder()
		h.ServeHTTP(w, req)
		return w
	}
	if got := request("gateway", ""); got.Code != 401 {
		t.Fatal("forged identity accepted", got.Code)
	}
	got := request("gateway", "Bearer real-web-token")
	if got.Code != 201 || strings.Contains(got.Body.String(), "never-echo-this") || strings.Contains(got.Body.String(), "secret_cipher") {
		t.Fatal("invalid secret boundary", got.Code, got.Body.String())
	}
	var c Config
	json.Unmarshal(got.Body.Bytes(), &c)
	if c.AgentConfigurationID != "published-v1" {
		t.Fatal("published config not frozen", c)
	}
	req := httptest.NewRequest("GET", "/internal/v1/contexts/unknown", nil)
	token, _ := sec.Mint("gateway", "channel")
	req.Header.Set("X-Service-Token", token)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != 403 {
		t.Fatal("non-auth introspection accepted", w.Code)
	}
}

type testVerifier struct{}

func (testVerifier) Agent(context.Context, string, string, []string) (string, error) {
	return "published-v1", nil
}
func (testVerifier) Audience(context.Context, string, string, string, string, []string) error {
	return nil
}
