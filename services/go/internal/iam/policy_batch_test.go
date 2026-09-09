package iam

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

type beforePolicyBatch struct {
	once   sync.Once
	change func()
}

func (b *beforePolicyBatch) TraceQueryStart(ctx context.Context, conn *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
	if strings.HasPrefix(data.SQL, "WITH requested") {
		b.once.Do(b.change)
	}
	return ctx
}
func (*beforePolicyBatch) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func TestPostgresPolicyBatchUsesOneEpochAndFencesCapabilities(t *testing.T) {
	store := integrationStore(t)
	ctx := context.Background()
	if _, err := store.Ensure(ctx, "admin", "admin", ""); err != nil {
		t.Fatal(err)
	}
	space, err := store.CreateSpace(ctx, "admin", "Batch")
	if err != nil {
		t.Fatal(err)
	}
	if err = store.RegisterSourceResource(ctx, "admin", space.ID, "source:one"); err != nil {
		t.Fatal(err)
	}
	public, private, _ := ed25519.GenerateKey(rand.Reader)
	sec := platform.NewServiceSecurity(private, map[string]ed25519.PublicKey{"iam": public, "retrieval": public, "graphiti": public, "gateway": public, "agent": public})
	handler := NewHandler(store, platform.NewClient("iam", sec), "http://unused", sec)
	call := func(caller string, body any, want int) map[string]any {
		t.Helper()
		raw, _ := json.Marshal(body)
		r := httptest.NewRequest("POST", "/internal/v1/policies/batch", bytes.NewReader(raw))
		token, _ := sec.Mint(caller, "iam")
		r.Header.Set("X-Service-Token", token)
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != want {
			t.Fatalf("caller %s status %d want %d", caller, w.Code, want)
		}
		var out map[string]any
		_ = json.Unmarshal(w.Body.Bytes(), &out)
		return out
	}
	resources := []platform.Resource{{SpaceID: space.ID, ResourceID: "source:one"}, {SpaceID: space.ID, ResourceID: "source:missing"}, {SpaceID: "missing-space"}}
	for _, caller := range []string{"gateway", "agent"} {
		call(caller, map[string]any{"resources": resources}, 403)
	}
	call("retrieval", map[string]any{"resources": resources, "unknown": true}, 400)
	call("retrieval", map[string]any{"resources": []platform.Resource{resources[0], resources[0]}}, 400)
	first := call("graphiti", map[string]any{"resources": resources}, 200)
	items := first["items"].([]any)
	if len(items) != 3 || !items[0].(map[string]any)["found"].(bool) || items[1].(map[string]any)["found"].(bool) || items[2].(map[string]any)["found"].(bool) {
		t.Fatal("incorrect missing-resource decisions")
	}
	var changed error
	tracer := &beforePolicyBatch{change: func() { changed = store.SetGrant(ctx, "admin", space.ID, "", "read", []string{}) }}
	config := store.Pool.Config()
	config.ConnConfig.Tracer = tracer
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	snap, err := NewStore(pool, "admin").Policies(ctx, resources)
	if err != nil {
		t.Fatal(err)
	}
	if changed != nil {
		t.Fatal(changed)
	}
	if snap.AuthEpoch != int64(first["auth_epoch"].(float64)) || len(snap.Items[0].Policy.SpaceReadSubjects) != 1 {
		t.Fatal("mixed epochs in batch")
	}
	next, err := store.Policies(ctx, resources)
	if err != nil {
		t.Fatal(err)
	}
	if next.AuthEpoch <= snap.AuthEpoch || len(next.Items[0].Policy.SpaceReadSubjects) != 0 {
		t.Fatal("fresh batch ignored revocation")
	}
	for _, item := range next.Items {
		if item.Found && item.Policy.AuthEpoch != next.AuthEpoch {
			t.Fatal("item epoch differs")
		}
	}
}
