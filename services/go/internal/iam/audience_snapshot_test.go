package iam

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

func TestAudienceSnapshotReconcilesInactiveStateWithCurrentManagerAuthorization(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	for _, id := range []string{"admin", "other"} {
		if _, err := s.Ensure(ctx, id, id, ""); err != nil {
			t.Fatal(err)
		}
	}
	space, err := s.CreateSpace(ctx, "admin", "Audience reconcile")
	if err != nil {
		t.Fatal(err)
	}
	a, err := s.CreateAudience(ctx, "admin", ChannelAudience{ID: "audience", ChannelID: "bot", GroupKey: "group", SpaceIDs: []string{space.ID}})
	if err != nil {
		t.Fatal(err)
	}
	pub, key, _ := ed25519.GenerateKey(rand.Reader)
	sec := platform.NewServiceSecurity(key, map[string]ed25519.PublicKey{"iam": pub, "auth": pub, "channel": pub, "gateway": pub})
	auth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if who, e := sec.Verify(r.Header.Get("X-Service-Token"), "auth"); e != nil || who != "iam" {
			t.Error("missing workload identity")
		}
		bearer := r.Header.Get("Authorization")
		if bearer == "" {
			w.WriteHeader(401)
			return
		}
		if bearer == "unavailable" {
			w.WriteHeader(503)
			return
		}
		id := "admin"
		if bearer == "other" {
			id = "other"
		}
		p, e := s.Principal(ctx, id)
		if e != nil {
			w.WriteHeader(403)
			return
		}
		scopes := []string{"knowledge:read", "knowledge:write"}
		if bearer == "read-only" {
			scopes = []string{"knowledge:read"}
		}
		platform.JSON(w, 200, platform.Resolved{Principal: p, Scopes: scopes, Delegated: bearer == "delegated"})
	}))
	defer auth.Close()
	h := NewHandler(s, platform.NewClient("iam", sec), auth.URL, sec)
	call := func(caller, bearer, id string, want int) map[string]any {
		t.Helper()
		r := httptest.NewRequest("GET", "/internal/v1/channel-audiences/"+id, nil)
		if caller != "" {
			token, _ := sec.Mint(caller, "iam")
			r.Header.Set("X-Service-Token", token)
		}
		r.Header.Set("Authorization", bearer)
		r.Header.Set("X-Principal-ID", "admin")
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != want {
			t.Fatalf("caller=%s bearer=%s id=%s got=%d want=%d", caller, bearer, id, w.Code, want)
		}
		var out map[string]any
		if e := json.Unmarshal(w.Body.Bytes(), &out); e != nil {
			t.Fatal(e)
		}
		if want != 200 && out["space_ids"] != nil {
			t.Fatal("denied response exposes registry")
		}
		return out
	}
	for _, state := range []bool{true, false} {
		if !state {
			a, err = s.UpdateAudience(ctx, "admin", a.ID, a.ChannelID, a.GroupKey, a.Version, a.SpaceIDs, false)
			if err != nil {
				t.Fatal(err)
			}
		}
		before, err := s.Principal(ctx, "admin")
		if err != nil {
			t.Fatal(err)
		}
		out := call("channel", "admin", a.ID, 200)
		if out["id"] != a.ID || out["channel_id"] != a.ChannelID || out["group_key"] != a.GroupKey || out["active"] != state || out["version"] != float64(a.Version) || out["auth_epoch"] != float64(before.AuthEpoch) {
			t.Fatal("snapshot does not match committed audience state", out)
		}
		after, err := s.Principal(ctx, "admin")
		if err != nil || after.AuthEpoch != before.AuthEpoch {
			t.Fatal("read mutated IAM epoch", err)
		}
	}
	call("", "admin", a.ID, 401)
	call("gateway", "admin", a.ID, 403)
	call("auth", "admin", a.ID, 403)
	call("channel", "", a.ID, 401)
	call("channel", "delegated", a.ID, 403)
	call("channel", "read-only", a.ID, 403)
	call("channel", "other", a.ID, 403)
	call("channel", "unavailable", a.ID, 503)
	call("channel", "admin", "missing", 404)
	call("channel", "admin", "invalid*id", 400)
	for _, action := range []string{"read", "grant"} {
		if err = s.SetGrant(ctx, "admin", space.ID, "", action, []string{"user:other"}); err != nil {
			t.Fatal(err)
		}
		call("channel", "admin", a.ID, 403)
		if err = s.SetGrant(ctx, "other", space.ID, "", action, []string{"user:admin", "user:other"}); err != nil {
			t.Fatal(err)
		}
	}
}
