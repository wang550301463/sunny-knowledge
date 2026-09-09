package iam

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

func TestPostgresSourceRegistrationUsesDelegatedLivePermissions(t *testing.T) {
	store := integrationStore(t)
	ctx := context.Background()
	for _, id := range []string{"admin", "editor", "outsider"} {
		if _, err := store.Ensure(ctx, id, id, ""); err != nil {
			t.Fatal(err)
		}
	}
	space, err := store.CreateSpace(ctx, "admin", "Sources")
	if err != nil {
		t.Fatal(err)
	}
	for _, action := range []string{"read", "write"} {
		if err := store.SetGrant(ctx, "admin", space.ID, "", action, []string{"user:admin", "user:editor"}); err != nil {
			t.Fatal(err)
		}
	}
	pub, key, _ := ed25519.GenerateKey(rand.Reader)
	security := platform.NewServiceSecurity(key, map[string]ed25519.PublicKey{"iam": pub, "ingest": pub, "gateway": pub})
	auth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("Authorization")
		if id == "" {
			w.WriteHeader(401)
			return
		}
		p, err := store.Principal(ctx, id)
		if err != nil {
			w.WriteHeader(403)
			return
		}
		scopes := []string{"knowledge:read", "knowledge:write"}
		if id == "outsider" {
			scopes = []string{"knowledge:read"}
		}
		platform.JSON(w, 200, map[string]any{"principal": p, "scopes": scopes})
	}))
	defer auth.Close()
	handler := NewHandler(store, platform.NewClient("iam", security), auth.URL, security)
	call := func(caller, actor, id string, want int) {
		t.Helper()
		body, _ := json.Marshal(platform.Resource{SpaceID: space.ID, ResourceID: id})
		r := httptest.NewRequest("POST", "/internal/v1/source-resources", bytes.NewReader(body))
		token, _ := security.Mint(caller, "iam")
		r.Header.Set("X-Service-Token", token)
		r.Header.Set("Authorization", actor)
		r.Header.Set("X-Principal-ID", "admin")
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != want {
			t.Fatalf("caller=%s actor=%s resource=%s got=%d want=%d", caller, actor, id, w.Code, want)
		}
	}
	call("gateway", "admin", "source:test", 403)
	call("ingest", "", "source:test", 401)
	call("ingest", "outsider", "source:test", 403)
	call("ingest", "editor", "page:forbidden", 400)
	call("ingest", "editor", "source:test", 201)
	before, _ := store.Principal(ctx, "admin")
	call("ingest", "editor", "source:test", 201)
	after, _ := store.Principal(ctx, "admin")
	if before.AuthEpoch != after.AuthEpoch {
		t.Fatal("idempotent registration advanced epoch")
	}
	if err := store.SetGrant(ctx, "admin", space.ID, "source:test", "read", []string{"user:admin"}); err != nil {
		t.Fatal(err)
	}
	call("ingest", "editor", "source:test", 403)
	if err := store.SetGrant(ctx, "admin", space.ID, "", "write", []string{"user:admin"}); err != nil {
		t.Fatal(err)
	}
	call("ingest", "editor", "source:new", 403)
}
