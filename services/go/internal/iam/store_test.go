package iam

import (
	"context"
	"github.com/jackc/pgx/v5/pgxpool"
	"os"
	"testing"
)

func integrationStore(t *testing.T) *Store {
	t.Helper()
	dsn := os.Getenv("IAM_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("IAM_TEST_DATABASE_URL required for real PostgreSQL integration")
	}
	pool, err := pgxpool.New(context.Background(), dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	s := NewStore(pool, "admin")
	if err := s.Migrate(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(context.Background(), `TRUNCATE iam_audit,iam_policies,iam_resources,iam_spaces,iam_memberships,iam_groups,iam_users CASCADE; UPDATE iam_epoch SET value=0`); err != nil {
		t.Fatal(err)
	}
	return s
}
func TestPostgresLiveMembershipAndAdminSeparation(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	for _, id := range []string{"admin", "alice", "bob"} {
		if _, err := s.Ensure(ctx, id, id, ""); err != nil {
			t.Fatal(err)
		}
	}
	space, err := s.CreateSpace(ctx, "admin", "Engineering")
	if err != nil {
		t.Fatal(err)
	}
	if d, err := s.Check(ctx, "admin", "read", space.ID, ""); err != nil || !d.Allowed {
		t.Fatalf("creator grant missing %v %+v", err, d)
	}
	group, err := s.CreateGroup(ctx, "admin", "group", "Engineering")
	if err != nil {
		t.Fatal(err)
	}
	if err = s.SetMember(ctx, "admin", group.ID, "alice", true); err != nil {
		t.Fatal(err)
	}
	if err = s.SetGrant(ctx, "admin", space.ID, "", "read", []string{"group:" + group.ID}); err != nil {
		t.Fatal(err)
	}
	if d, err := s.Check(ctx, "admin", "read", space.ID, ""); err != nil || d.Allowed {
		t.Fatalf("admin has implicit content access %v %+v", err, d)
	}
	before, err := s.Check(ctx, "alice", "read", space.ID, "page")
	if err != nil || !before.Allowed {
		t.Fatalf("membership ignored %v %+v", err, before)
	}
	if err = s.RegisterResource(ctx, space.ID, "page"); err != nil {
		t.Fatal(err)
	}
	if err = s.SetGrant(ctx, "admin", space.ID, "page", "read", []string{}); err != nil {
		t.Fatal(err)
	}
	denied, err := s.Check(ctx, "alice", "read", space.ID, "page")
	if err != nil || denied.Allowed || denied.ACLDomain == before.ACLDomain {
		t.Fatalf("empty ACL did not deny %v %+v", err, denied)
	}
	if err = s.SetGrant(ctx, "admin", space.ID, "page", "read", nil); err != nil {
		t.Fatal(err)
	}
	if err = s.SetMember(ctx, "admin", group.ID, "alice", false); err != nil {
		t.Fatal(err)
	}
	revoked, err := s.Check(ctx, "alice", "read", space.ID, "page")
	if err != nil || revoked.Allowed || revoked.AuthEpoch <= before.AuthEpoch {
		t.Fatalf("revocation stale %v %+v", err, revoked)
	}
	if err = s.SetGrant(ctx, "bob", space.ID, "", "read", []string{"user:bob"}); err == nil {
		t.Fatal("non-granter updated ACL")
	}
	if err = s.SetUser(ctx, "admin", "alice", "Alice", false); err != nil {
		t.Fatal(err)
	}
	if _, err = s.Principal(ctx, "alice"); err == nil {
		t.Fatal("inactive user resolved")
	}
	var audits int
	if err = s.Pool.QueryRow(ctx, "SELECT count(*) FROM iam_audit").Scan(&audits); err != nil || audits < 8 {
		t.Fatalf("audit missing count=%d %v", audits, err)
	}
}
