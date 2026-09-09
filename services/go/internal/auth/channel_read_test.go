package auth

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

type readFixture struct {
	*delegationFixture
	userKey  *rsa.PrivateKey
	verifier *Verifier
}

func newReadFixture(t *testing.T) *readFixture {
	t.Helper()
	pub, private, _ := ed25519.GenerateKey(rand.Reader)
	_, delegationKey, _ := ed25519.GenerateKey(rand.Reader)
	registry := map[string]ed25519.PublicKey{}
	for _, service := range []string{"auth", "channel", "agent", "knowledge", "retrieval", "graphiti", "llm", "iam", "gateway", "mcp"} {
		registry[service] = pub
	}
	f := &readFixture{delegationFixture: &delegationFixture{t: t, channelStatus: 200, iamStatus: 200, audienceAllowed: true, actorAllowed: true, security: platform.NewServiceSecurity(private, registry)}}
	f.context = delegatedContext{ID: "original-context", UserID: "alice", ExternalUserID: "wecom-user", ChannelID: "bot", ChannelVersion: 1, BindingVersion: 1, ConversationKey: "isolated-conversation", Generation: 1, Capabilities: []string{"knowledge:read"}, MessageID: "original-message", AgentID: "agent-1", AgentConfigurationID: "config-1", SpaceIDs: []string{"s"}, ChatType: "group", ChatID: "wecom-group", AudienceID: "audience-1", GroupKey: "group-1", GroupVersion: 1, ExpiresAt: time.Now().Add(-24 * time.Hour).UTC()}
	channel := httptest.NewServer(f.security.Middleware("channel", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		if platform.Caller(r.Context()) != "auth" || r.URL.Path != "/internal/v1/runs/run-1/context" {
			t.Error("incorrect history introspection boundary")
		}
		platform.JSON(w, f.channelStatus, map[string]any{"run_id": "run-1", "context": f.context})
	})))
	t.Cleanup(channel.Close)
	iam := httptest.NewServer(f.security.Middleware("iam", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		if f.iamStatus != 200 {
			w.WriteHeader(f.iamStatus)
			return
		}
		switch r.URL.Path {
		case "/internal/v1/principals/ensure":
			var in map[string]string
			json.NewDecoder(r.Body).Decode(&in)
			platform.JSON(w, 200, platform.Principal{ID: in["id"], Subjects: []string{"user:" + in["id"]}, AuthEpoch: 7})
		case "/internal/v1/principals/alice":
			platform.JSON(w, 200, platform.Principal{ID: "alice", Subjects: []string{"user:alice"}, Permissions: []string{"platform_admin"}, AuthEpoch: 7})
		case "/internal/v1/channel-audiences/verify":
			platform.JSON(w, 200, map[string]any{"allowed": f.audienceAllowed, "version": 1, "auth_epoch": 7})
		case "/internal/v1/check-channel":
			var in map[string]string
			json.NewDecoder(r.Body).Decode(&in)
			if in["audience_id"] != "audience-1" || in["principal_id"] != "alice" {
				t.Error("group read constraint lost")
			}
			platform.JSON(w, 200, platform.Decision{Allowed: f.actorAllowed, AuthEpoch: 7})
		default:
			t.Errorf("unexpected IAM operation %s", r.URL.Path)
			w.WriteHeader(404)
		}
	})))
	t.Cleanup(iam.Close)
	f.broker, _ = NewDelegationBroker(delegationKey, channel.URL)
	f.userKey, _ = rsa.GenerateKey(rand.Reader, 2048)
	f.verifier = NewVerifier("https://idp.example/realm", "", "knowledge-api")
	f.verifier.keys = map[string]*rsa.PublicKey{"user-key": &f.userKey.PublicKey}
	f.verifier.loaded = time.Now()
	f.http = NewHandler(f.verifier, platform.NewClient("auth", f.security), iam.URL, f.security, WithDelegation(f.broker))
	return f
}

func (f *readFixture) web(subject, scope string, expiry time.Time) string {
	claims := jwt.MapClaims{"iss": f.verifier.Issuer, "sub": subject, "aud": []string{"knowledge-api"}, "azp": "knowledge-web", "scope": scope, "iat": time.Now().Unix(), "exp": expiry.Unix()}
	token := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	token.Header["kid"] = "user-key"
	raw, err := token.SignedString(f.userKey)
	if err != nil {
		f.t.Fatal(err)
	}
	return raw
}

func (f *readFixture) readToken() string {
	f.t.Helper()
	expiry := time.Now().Add(time.Minute)
	r := f.call("agent", "/internal/v1/channel-run-token", f.web("alice", "knowledge:read", expiry), `{"run_id":"run-1"}`)
	if r.Code != 200 {
		f.t.Fatalf("history token status %d", r.Code)
	}
	var result struct {
		AccessToken string `json:"access_token"`
		Scope       string `json:"scope"`
		ExpiresIn   int64  `json:"expires_in"`
	}
	if json.Unmarshal(r.Body.Bytes(), &result) != nil || !strings.HasPrefix(result.AccessToken, "skr_") || result.Scope != "knowledge:read" || result.ExpiresIn < 1 || result.ExpiresIn > 60 || r.Header().Get("Cache-Control") != "no-store" {
		f.t.Fatal("invalid bounded history credential")
	}
	return result.AccessToken
}

func TestChannelRunReadTokenRequiresDirectOwnerAndInternalAgent(t *testing.T) {
	f := newReadFixture(t)
	web := f.web("alice", "knowledge:read", time.Now().Add(time.Minute))
	for _, caller := range []string{"gateway", "mcp", "channel", "knowledge"} {
		if r := f.call(caller, "/internal/v1/channel-run-token", web, `{"run_id":"run-1"}`); r.Code != 403 {
			t.Fatalf("unauthorized exchange caller=%s status=%d", caller, r.Code)
		}
	}
	for _, body := range []string{`{"run_id":"run-1","user_id":"alice"}`, `{"run_id":"../x"}`, `null`} {
		if r := f.call("agent", "/internal/v1/channel-run-token", web, body); r.Code != 400 {
			t.Fatalf("invalid body status %d", r.Code)
		}
	}
	for _, tc := range []struct {
		subject, scope string
		want           int
	}{{"bob", "knowledge:read", 403}, {"alice", "knowledge:write", 403}} {
		if r := f.call("agent", "/internal/v1/channel-run-token", f.web(tc.subject, tc.scope, time.Now().Add(time.Minute)), `{"run_id":"run-1"}`); r.Code != tc.want {
			t.Fatalf("direct owner/scope not enforced %d", r.Code)
		}
	}
	token := f.readToken()
	if r := f.call("agent", "/internal/v1/channel-run-token", token, `{"run_id":"run-1"}`); r.Code != 403 {
		t.Fatal("history credential re-delegated", r.Code)
	}
	for _, caller := range []string{"agent", "knowledge"} {
		r := f.call(caller, "/internal/v1/resolve", token, `{}`)
		var out map[string]any
		if r.Code != 200 || json.Unmarshal(r.Body.Bytes(), &out) != nil {
			t.Fatalf("read resolve %d", r.Code)
		}
		p := out["principal"].(map[string]any)
		c := p["channel_context"].(map[string]any)
		if c["read_run_id"] != "run-1" || c["context_id"] != "original-context" || c["audience_id"] != "audience-1" || len(p["permissions"].([]any)) != 0 || out["delegated"] != true {
			t.Fatal("read-only constraint missing")
		}
	}
	for _, caller := range []string{"gateway", "mcp", "channel", "retrieval", "graphiti", "llm", "iam"} {
		if r := f.call(caller, "/internal/v1/resolve", token, `{}`); r.Code != 403 {
			t.Fatalf("history credential accepted by %s: %d", caller, r.Code)
		}
	}
	for _, action := range []string{"write", "grant", "review"} {
		r := f.call("knowledge", "/internal/v1/authorize", token, `{"action":"`+action+`","space_id":"s"}`)
		var out platform.Decision
		json.Unmarshal(r.Body.Bytes(), &out)
		if r.Code != 200 || out.Allowed {
			t.Fatal("read credential elevated", r.Code)
		}
	}
}

func TestChannelRunReadTokenRechecksBindingAudienceAndSourcePermissions(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mutate func(*readFixture)
		want   int
	}{
		{"unbind", func(f *readFixture) { f.channelStatus = 403 }, 403},
		{"group disabled", func(f *readFixture) { f.audienceAllowed = false }, 403},
		{"group unavailable", func(f *readFixture) { f.iamStatus = 503 }, 503},
		{"account disabled", func(f *readFixture) { f.iamStatus = 403 }, 403},
		{"scope changed", func(f *readFixture) { f.context.SpaceIDs = []string{"other"} }, 403},
		{"generation changed", func(f *readFixture) { f.context.Generation++ }, 403},
		{"binding changed", func(f *readFixture) { f.context.BindingVersion++ }, 403},
		{"configuration changed", func(f *readFixture) { f.context.AgentConfigurationID = "other" }, 403},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newReadFixture(t)
			token := f.readToken()
			f.mu.Lock()
			tc.mutate(f)
			f.mu.Unlock()
			if r := f.call("knowledge", "/internal/v1/resolve", token, `{}`); r.Code != tc.want {
				t.Fatalf("stale history status %d want %d", r.Code, tc.want)
			}
		})
	}
	f := newReadFixture(t)
	token := f.readToken()
	f.mu.Lock()
	f.actorAllowed = false
	f.mu.Unlock()
	r := f.call("knowledge", "/internal/v1/authorize", token, `{"action":"read","space_id":"s","resource_id":"private-source"}`)
	var out platform.Decision
	json.Unmarshal(r.Body.Bytes(), &out)
	if r.Code != 200 || out.Allowed {
		t.Fatal("source read revocation bypassed", r.Code)
	}
}
