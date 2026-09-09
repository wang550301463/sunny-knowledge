package iam

import (
	"context"
	"errors"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"slices"
	"strings"
	"sync"
	"testing"
)

type beforeGrantList struct {
	once   sync.Once
	revoke func()
}

func (t *beforeGrantList) TraceQueryStart(ctx context.Context, conn *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
	if strings.HasPrefix(data.SQL, "SELECT space_id,resource_id,action,subjects,version FROM iam_policies") {
		t.once.Do(t.revoke)
	}
	return ctx
}
func (t *beforeGrantList) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func TestPostgresGrantListUsesAuthorizationSnapshot(t *testing.T) {
	base := integrationStore(t)
	ctx := context.Background()
	for _, id := range []string{"admin", "bob"} {
		if _, err := base.Ensure(ctx, id, id, ""); err != nil {
			t.Fatal(err)
		}
	}
	space, err := base.CreateSpace(ctx, "admin", "Private")
	if err != nil {
		t.Fatal(err)
	}
	var revokeErr error
	tracer := &beforeGrantList{revoke: func() {
		// Commit policy changes after the request's authorization query but immediately
		// before its list query. The returned list must belong to the authorized snapshot.
		if err := base.SetGrant(ctx, "admin", space.ID, "", "read", []string{"user:bob"}); err != nil {
			revokeErr = err
			return
		}
		revokeErr = base.SetGrant(ctx, "admin", space.ID, "", "grant", []string{})
	}}
	config := base.Pool.Config()
	config.ConnConfig.Tracer = tracer
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	store := NewStore(pool, "admin")
	grants, err := store.AuthorizedGrants(ctx, "admin", space.ID, "")
	if err != nil {
		t.Fatal(err)
	}
	if revokeErr != nil {
		t.Fatal(revokeErr)
	}
	for _, grant := range grants {
		if grant.Action == "read" && !slices.Equal(grant.Subjects, []string{"user:admin"}) {
			t.Fatalf("grant listing leaked a newer unauthorized policy: %+v", grant)
		}
	}
	if _, err = store.AuthorizedGrants(ctx, "admin", space.ID, ""); !errors.Is(err, ErrDenied) {
		t.Fatalf("subsequent revoked request was not denied: %v", err)
	}
}
