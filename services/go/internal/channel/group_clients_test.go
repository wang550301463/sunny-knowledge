package channel

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

func TestAudienceSnapshotClientRequiresCompleteStrictAuthorityReceipt(t *testing.T) {
	good := map[string]any{"id": "audience", "channel_id": "channel", "group_key": "group", "space_ids": []string{"s"}, "active": false, "version": 2, "auth_epoch": 5}
	cases := map[string]string{}
	for name := range good {
		for _, mode := range []string{"missing", "null", "wrong-type"} {
			copy := map[string]any{}
			for k, v := range good {
				copy[k] = v
			}
			switch mode {
			case "missing":
				delete(copy, name)
			case "null":
				copy[name] = nil
			default:
				copy[name] = map[string]any{}
			}
			raw, _ := json.Marshal(copy)
			cases[name+"-"+mode] = string(raw)
		}
	}
	for name, value := range map[string]any{"duplicate-spaces": []string{"s", "s"}, "empty-spaces": []string{}, "invalid-space": []string{"bad/space"}} {
		copy := map[string]any{}
		for k, v := range good {
			copy[k] = v
		}
		copy["space_ids"] = value
		raw, _ := json.Marshal(copy)
		cases[name] = string(raw)
	}
	raw, _ := json.Marshal(good)
	cases["duplicate-identity"] = string(raw[:len(raw)-1]) + `,"id":"audience"}`
	cases["unknown-field"] = string(raw[:len(raw)-1]) + `,"user_id":"admin"}`
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	sec := platform.NewServiceSecurity(priv, map[string]ed25519.PublicKey{"channel": pub, "iam": pub})
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.Write([]byte(body))
			}))
			defer server.Close()
			v := HTTPVerifier{Client: platform.NewClient("channel", sec), IAMURL: server.URL}
			if _, e := v.ReadAudience(context.Background(), "Bearer user", "audience"); !errors.Is(e, ErrUnavailable) {
				t.Fatal("malformed authority receipt accepted", e)
			}
		})
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if caller, e := sec.Verify(r.Header.Get("X-Service-Token"), "iam"); e != nil || caller != "channel" || r.Header.Get("Authorization") != "Bearer actual-user" {
			w.WriteHeader(403)
			return
		}
		platform.JSON(w, 200, good)
	}))
	defer server.Close()
	v := HTTPVerifier{Client: platform.NewClient("channel", sec), IAMURL: server.URL}
	a, e := v.ReadAudience(context.Background(), "Bearer actual-user", "audience")
	if e != nil || a.Active || a.Version != 2 {
		t.Fatal("inactive authoritative snapshot rejected", e)
	}
	if _, e = v.ReadAudience(context.Background(), "", "audience"); !errors.Is(e, ErrDenied) {
		t.Fatal("missing actual actor accepted", e)
	}
}
