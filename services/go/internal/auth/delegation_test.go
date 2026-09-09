package auth

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

type delegationFixture struct {
	t                             *testing.T
	mu                            sync.Mutex
	context                       delegatedContext
	channelStatus, iamStatus      int
	audienceAllowed, actorAllowed bool
	http                          http.Handler
	security                      *platform.ServiceSecurity
}

func newDelegationFixture(t *testing.T) *delegationFixture {
	t.Helper()
	pub, private, _ := ed25519.GenerateKey(rand.Reader)
	_, delegationKey, _ := ed25519.GenerateKey(rand.Reader)
	registry := map[string]ed25519.PublicKey{}
	for _, service := range []string{"auth", "channel", "agent", "knowledge", "retrieval", "graphiti", "llm", "iam", "gateway", "mcp"} {
		registry[service] = pub
	}
	f := &delegationFixture{t: t, channelStatus: 200, iamStatus: 200, audienceAllowed: true, actorAllowed: true, security: platform.NewServiceSecurity(private, registry)}
	f.context = delegatedContext{ID: "context-1", UserID: "alice", ExternalUserID: "wecom-user", ChannelID: "bot", ChannelVersion: 1, BindingVersion: 1, ConversationKey: "isolated-conversation", Generation: 1, Capabilities: []string{"knowledge:read"}, MessageID: "message-1", AgentID: "agent-1", AgentConfigurationID: "config-1", SpaceIDs: []string{"s"}, ChatType: "group", ChatID: "wecom-group", AudienceID: "audience-1", GroupKey: "group-1", GroupVersion: 1, ExpiresAt: time.Now().Add(170 * time.Second).UTC()}
	channel := httptest.NewServer(f.security.Middleware("channel", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		if platform.Caller(r.Context()) != "auth" || r.URL.Path != "/internal/v1/contexts/context-1" {
			t.Error("broker did not introspect the opaque context through authenticated channel")
		}
		platform.JSON(w, f.channelStatus, f.context)
	})))
	t.Cleanup(channel.Close)
	iam := httptest.NewServer(f.security.Middleware("iam", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		if platform.Caller(r.Context()) != "auth" {
			t.Error("IAM call is not auth workload")
		}
		if f.iamStatus != 200 {
			w.WriteHeader(f.iamStatus)
			return
		}
		switch r.URL.Path {
		case "/internal/v1/principals/alice":
			platform.JSON(w, 200, platform.Principal{ID: "alice", Subjects: []string{"user:alice", "group:engineering"}, Permissions: []string{"platform_admin"}, AuthEpoch: 7})
		case "/internal/v1/channel-audiences/verify":
			platform.JSON(w, 200, map[string]any{"allowed": f.audienceAllowed, "version": 1, "auth_epoch": 7})
		case "/internal/v1/check-channel", "/internal/v1/check":
			var in map[string]string
			if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
				t.Error(err)
			}
			if in["principal_id"] != "alice" {
				t.Error("caller-supplied principal used")
			}
			if f.context.ChatType == "group" && (r.URL.Path != "/internal/v1/check-channel" || in["audience_id"] != "audience-1" || in["group_key"] != "group-1" || in["channel_id"] != "bot") {
				t.Error("group constraint lost")
			}
			platform.JSON(w, 200, platform.Decision{Allowed: f.actorAllowed, AuthEpoch: 7})
		default:
			t.Errorf("unexpected IAM path %s", r.URL.Path)
			w.WriteHeader(404)
		}
	})))
	t.Cleanup(iam.Close)
	broker, err := NewDelegationBroker(delegationKey, channel.URL)
	if err != nil {
		t.Fatal(err)
	}
	f.http = NewHandler(NewVerifier("", "", ""), platform.NewClient("auth", f.security), iam.URL, f.security, WithDelegation(broker))
	return f
}

func (f *delegationFixture) call(caller, path, token, body string) *httptest.ResponseRecorder {
	f.t.Helper()
	r := httptest.NewRequest("POST", path, bytes.NewBufferString(body))
	workload, _ := f.security.Mint(caller, "auth")
	r.Header.Set("X-Service-Token", workload)
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	r.Header.Set("X-Principal-ID", "admin")
	w := httptest.NewRecorder()
	f.http.ServeHTTP(w, r)
	return w
}

func (f *delegationFixture) token() string {
	f.t.Helper()
	r := f.call("channel", "/internal/v1/channel-token", "", `{"context_id":"context-1"}`)
	if r.Code != 200 {
		f.t.Fatalf("exchange failed: status %d", r.Code)
	}
	var result struct {
		AccessToken string `json:"access_token"`
		TokenType   string `json:"token_type"`
		ExpiresIn   int    `json:"expires_in"`
	}
	if err := json.Unmarshal(r.Body.Bytes(), &result); err != nil || result.AccessToken == "" || result.TokenType != "Bearer" || result.ExpiresIn < 1 || result.ExpiresIn > 180 {
		f.t.Fatal("invalid token exchange result")
	}
	if r.Header().Get("Cache-Control") != "no-store" {
		f.t.Fatal("token response is cacheable")
	}
	return result.AccessToken
}

func TestChannelExchangeRequiresWorkloadAndOpaqueContextOnly(t *testing.T) {
	f := newDelegationFixture(t)
	for _, caller := range []string{"gateway", "mcp", "agent", "iam"} {
		if r := f.call(caller, "/internal/v1/channel-token", "", `{"context_id":"context-1"}`); r.Code != 403 {
			t.Fatalf("exchange accepted caller %s: %d", caller, r.Code)
		}
	}
	for _, body := range []string{`{"context_id":"context-1","user_id":"admin"}`, `{"context_id":"../escape"}`, `null`} {
		if r := f.call("channel", "/internal/v1/channel-token", "", body); r.Code != 400 {
			t.Fatalf("exchange accepted invalid body: %d", r.Code)
		}
	}
	f.token()
}

func TestChannelTokenPreservesSeparateAudienceAndCannotElevateScope(t *testing.T) {
	f := newDelegationFixture(t)
	token := f.token()
	r := f.call("agent", "/internal/v1/resolve", token, `{}`)
	var result platform.Resolved
	if r.Code != 200 || json.Unmarshal(r.Body.Bytes(), &result) != nil {
		t.Fatalf("resolve %d", r.Code)
	}
	if !result.Delegated || result.Principal.ChannelContext == nil || result.Principal.ChannelContext.AudienceID != "audience-1" || result.Principal.ChannelContext.AgentConfigurationID != "config-1" || len(result.Principal.Permissions) != 0 || strings.Contains(strings.Join(result.Principal.Subjects, " "), "audience:") || strings.Join(result.Scopes, " ") != "knowledge:read" {
		t.Fatal("delegation lost constraints or enlarged identity")
	}
	for _, tc := range []struct {
		action, space string
		allowed       bool
	}{{"read", "s", true}, {"read", "outside", false}, {"write", "s", false}, {"grant", "s", false}, {"review", "s", false}} {
		body, _ := json.Marshal(platform.AuthorizationRequest{Action: tc.action, SpaceID: tc.space, ResourceID: "page"})
		r := f.call("knowledge", "/internal/v1/authorize", token, string(body))
		var d platform.Decision
		if r.Code != 200 || json.Unmarshal(r.Body.Bytes(), &d) != nil || d.Allowed != tc.allowed {
			t.Fatalf("incorrect %s/%s decision: %d", tc.action, tc.space, r.Code)
		}
	}
	for _, caller := range []string{"gateway", "mcp"} {
		if r := f.call(caller, "/internal/v1/resolve", token, `{}`); r.Code != 403 {
			t.Fatalf("internal delegation accepted at public ingress %s: %d", caller, r.Code)
		}
	}
}

func TestChannelTokenRevalidatesContextAndLiveIAMOnEveryRead(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mutate func(*delegationFixture)
		status int
	}{
		{"binding revoked", func(f *delegationFixture) { f.channelStatus = 403 }, 403},
		{"lease unavailable", func(f *delegationFixture) { f.channelStatus = 503 }, 503},
		{"channel changed", func(f *delegationFixture) { f.context.ChannelVersion++ }, 403},
		{"conversation cleared", func(f *delegationFixture) { f.context.Generation++ }, 403},
		{"agent version changed", func(f *delegationFixture) { f.context.AgentConfigurationID = "new" }, 403},
		{"binding moved", func(f *delegationFixture) { f.context.UserID = "other" }, 403},
		{"IAM outage", func(f *delegationFixture) { f.iamStatus = 503 }, 503},
		{"account disabled", func(f *delegationFixture) { f.iamStatus = 403 }, 403},
		{"audience disabled", func(f *delegationFixture) { f.audienceAllowed = false }, 403},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newDelegationFixture(t)
			token := f.token()
			f.mu.Lock()
			tc.mutate(f)
			f.mu.Unlock()
			r := f.call("retrieval", "/internal/v1/resolve", token, `{}`)
			if r.Code != tc.status {
				t.Fatalf("stale context status %d want %d", r.Code, tc.status)
			}
		})
	}
}

func TestPrivateChannelStillRequiresActorAndFrozenScope(t *testing.T) {
	f := newDelegationFixture(t)
	f.context.ChatType = "single"
	f.context.ChatID = ""
	f.context.AudienceID = ""
	f.context.GroupKey = ""
	f.context.GroupVersion = 0
	token := f.token()
	f.mu.Lock()
	f.actorAllowed = false
	f.mu.Unlock()
	r := f.call("knowledge", "/internal/v1/authorize", token, `{"action":"read","space_id":"s","resource_id":"private"}`)
	var d platform.Decision
	if r.Code != 200 || json.Unmarshal(r.Body.Bytes(), &d) != nil || d.Allowed {
		t.Fatal("private channel bypasses actor revocation")
	}
}
