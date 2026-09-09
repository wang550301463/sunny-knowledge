package iam

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

func TestAudienceHTTPRequiresChannelWorkloadDirectActorAndDisclosure(t *testing.T) {
	store := integrationStore(t)
	ctx := context.Background()
	for _, id := range []string{"admin", "reader"} {
		if _, err := store.Ensure(ctx, id, id, ""); err != nil {
			t.Fatal(err)
		}
	}
	space, err := store.CreateSpace(ctx, "admin", "Channel disclosure")
	if err != nil {
		t.Fatal(err)
	}
	pub, key, _ := ed25519.GenerateKey(rand.Reader)
	security := platform.NewServiceSecurity(key, map[string]ed25519.PublicKey{"iam": pub, "channel": pub, "gateway": pub, "auth": pub})
	auth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if caller, err := security.Verify(r.Header.Get("X-Service-Token"), "auth"); err != nil || caller != "iam" {
			t.Error("missing IAM workload")
		}
		bearer := r.Header.Get("Authorization")
		if bearer == "" {
			w.WriteHeader(401)
			return
		}
		id := strings.TrimPrefix(bearer, "delegated-")
		p, err := store.Principal(ctx, id)
		if err != nil {
			w.WriteHeader(403)
			return
		}
		scopes := []string{"knowledge:read"}
		if id == "admin" {
			scopes = append(scopes, "knowledge:write")
		}
		platform.JSON(w, 200, platform.Resolved{Principal: p, Scopes: scopes, Delegated: strings.HasPrefix(bearer, "delegated-")})
	}))
	defer auth.Close()
	handler := NewHandler(store, platform.NewClient("iam", security), auth.URL, security)
	call := func(caller, actor, path string, body any, want int) map[string]any {
		t.Helper()
		raw, _ := json.Marshal(body)
		request := httptest.NewRequest("POST", path, bytes.NewReader(raw))
		if caller != "" {
			token, _ := security.Mint(caller, "iam")
			request.Header.Set("X-Service-Token", token)
		}
		request.Header.Set("Authorization", actor)
		request.Header.Set("X-Principal-ID", "admin")
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != want {
			t.Fatalf("caller=%s actor=%s path=%s got=%d want=%d", caller, actor, path, response.Code, want)
		}
		var result map[string]any
		if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		return result
	}
	path := "/internal/v1/channel-audiences"
	body := map[string]any{"id": "a", "channel_id": "c", "group_key": "g", "space_ids": []string{space.ID}, "acknowledged_public_to_group": true}
	call("", "admin", path, body, 401)
	call("gateway", "admin", path, body, 403)
	call("channel", "", path, body, 401)
	call("channel", "reader", path, body, 403)
	call("channel", "delegated-admin", path, body, 403)
	body["acknowledged_public_to_group"] = false
	call("channel", "admin", path, body, 400)
	body["acknowledged_public_to_group"] = true
	body["principal_id"] = "admin"
	call("channel", "admin", path, body, 400)
	delete(body, "principal_id")
	call("channel", "admin", path, body, 201)
	verify := map[string]any{"audience_id": "a", "channel_id": "c", "group_key": "g", "space_ids": []string{space.ID}}
	call("gateway", "admin", path+"/verify", verify, 403)
	if result := call("channel", "", path+"/verify", verify, 200); result["allowed"] != true {
		t.Fatal("registered group is unavailable")
	}
	check := map[string]any{"principal_id": "admin", "audience_id": "a", "channel_id": "c", "group_key": "g", "space_id": space.ID, "action": "read"}
	call("channel", "admin", "/internal/v1/check-channel", check, 403)
	if result := call("auth", "", "/internal/v1/check-channel", check, 200); result["allowed"] != true {
		t.Fatal("auth cannot check current group intersection")
	}
}
