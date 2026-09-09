package channel

import (
	"bytes"
	"context"
	"errors"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"os"
	"strings"
	"testing"
	"time"
)

func integrationStore(t *testing.T) *Store {
	t.Helper()
	dsn := os.Getenv("TEST_CHANNEL_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_CHANNEL_DATABASE_URL required for real PostgreSQL")
	}
	ctx := context.Background()
	base, e := pgxpool.New(ctx, dsn)
	if e != nil {
		t.Fatal("database configuration")
	}
	schema := "channel_test_" + strings.ReplaceAll(platform.ID(), "-", "")
	if _, e = base.Exec(ctx, "CREATE SCHEMA "+schema); e != nil {
		t.Fatal(e)
	}
	cfg, e := pgxpool.ParseConfig(dsn)
	if e != nil {
		t.Fatal("database configuration")
	}
	cfg.ConnConfig.RuntimeParams["search_path"] = schema
	pool, e := pgxpool.NewWithConfig(ctx, cfg)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { pool.Close(); base.Exec(ctx, "DROP SCHEMA "+schema+" CASCADE"); base.Close() })
	box, _ := NewSecretBox(bytes.Repeat([]byte{1}, 32))
	s := NewStore(pool, box)
	if e = s.Migrate(ctx); e != nil {
		t.Fatal(e)
	}
	return s
}
func configured(t *testing.T, s *Store) Config {
	t.Helper()
	v, e := s.SaveConfig(context.Background(), "admin", "", ConfigInput{Name: "bot", BotID: "bot", BotSecret: "secret", AgentID: "a", SpaceIDs: []string{"s"}})
	if e != nil {
		t.Fatal(e)
	}
	return v
}
func enabled(t *testing.T, s *Store) Config {
	t.Helper()
	v := configured(t, s)
	_, e := s.Pool.Exec(context.Background(), "UPDATE channel_configs SET enabled=true WHERE id=$1", v.ID)
	if e != nil {
		t.Fatal(e)
	}
	v.Enabled = true
	return v
}
func TestPostgresConfigCASLeaseFencingAndDedup(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	c := enabled(t, s)
	l, e := s.Acquire(ctx, c.ID, "worker1", time.Second)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.Acquire(ctx, c.ID, "worker2", time.Second); !errors.Is(e, ErrLeaseLost) {
		t.Fatal("double connection", e)
	}
	m := Message{ID: "m", RequestID: "r", UserID: "u", ChatType: "single", Text: "question"}
	if fresh, e := s.Accept(ctx, l, m); e != nil || !fresh {
		t.Fatal(e)
	}
	if fresh, e := s.Accept(ctx, l, m); e != nil || fresh {
		t.Fatal("duplicate", e)
	}
	s.Pool.Exec(ctx, "UPDATE channel_leases SET until_at=now()-interval '1 second' WHERE channel_id=$1", c.ID)
	l2, e := s.Acquire(ctx, c.ID, "worker2", time.Second)
	if e != nil || l2.Fence <= l.Fence {
		t.Fatal("fence failed", e)
	}
	if e = s.CheckLease(ctx, l); !errors.Is(e, ErrLeaseLost) {
		t.Fatal("stale lease accepted", e)
	}
	if _, e = s.Accept(ctx, l, Message{ID: "late"}); !errors.Is(e, ErrLeaseLost) {
		t.Fatal("late message accepted", e)
	}
	_, e = s.SaveConfig(ctx, "admin", c.ID, ConfigInput{BaseVersion: 99, Name: "changed", BotID: c.BotID, AgentID: "a", SpaceIDs: []string{"s"}})
	if !errors.Is(e, ErrConflict) {
		t.Fatal("stale config accepted", e)
	}
}
func TestPostgresBindingChallengeDoubleProofReplayAndRevocation(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	c := enabled(t, s)
	l, e := s.Acquire(ctx, c.ID, "worker", time.Minute)
	if e != nil {
		t.Fatal(e)
	}
	ch, e := s.BeginChallenge(ctx, l, "u")
	if e != nil {
		t.Fatal(e)
	}
	proof, e := s.ClaimChallenge(ctx, "alice", ch.ID, ch.WebToken)
	if e != nil {
		t.Fatal(e)
	}
	if e = s.ConfirmChallenge(ctx, l, "other", ch.ID, proof); !errors.Is(e, ErrDenied) {
		t.Fatal("wrong external user", e)
	}
	if e = s.ConfirmChallenge(ctx, l, "u", ch.ID, proof); e != nil {
		t.Fatal(e)
	}
	if e = s.ConfirmChallenge(ctx, l, "u", ch.ID, proof); !errors.Is(e, ErrDenied) {
		t.Fatal("replay", e)
	}
	m := Message{ID: "m", RequestID: "r", UserID: "u", ChatType: "single", Text: "q"}
	s.Accept(ctx, l, m)
	v, e := s.CreateContext(ctx, l, m)
	if e != nil || v.UserID != "alice" {
		t.Fatal("bound context", e)
	}
	if _, e = s.Introspect(ctx, v.ID); e != nil {
		t.Fatal(e)
	}
	if e = s.Unbind(ctx, "bob", c.ID, "u"); !errors.Is(e, ErrDenied) {
		t.Fatal("other user unbound", e)
	}
	if e = s.Unbind(ctx, "alice", c.ID, "u"); e != nil {
		t.Fatal(e)
	}
	if _, e = s.Introspect(ctx, v.ID); !errors.Is(e, ErrDenied) {
		t.Fatal("revoked context still active", e)
	}
}
func TestPostgresGroupRequiresRegistrationAndScopeIntersection(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	c := enabled(t, s)
	l, _ := s.Acquire(ctx, c.ID, "w", time.Minute)
	ch, _ := s.BeginChallenge(ctx, l, "u")
	proof, _ := s.ClaimChallenge(ctx, "alice", ch.ID, ch.WebToken)
	s.ConfirmChallenge(ctx, l, "u", ch.ID, proof)
	m := Message{ID: "g", RequestID: "r", UserID: "u", ChatType: "group", ChatID: "chat", Text: "q"}
	s.Accept(ctx, l, m)
	if _, e := s.CreateContext(ctx, l, m); !errors.Is(e, ErrDenied) {
		t.Fatal("unregistered group context", e)
	}
	g, e := s.SaveGroup(ctx, "admin", c.ID, "chat", GroupInput{AudienceID: "audience", SpaceIDs: []string{"s"}, Enabled: true})
	if e != nil {
		t.Fatal(e)
	}
	v, e := s.CreateContext(ctx, l, m)
	if e != nil || v.AudienceID != "audience" || v.GroupKey != g.ID {
		t.Fatal("group context", e)
	}
	g.Enabled = false
	_, e = s.SaveGroup(ctx, "admin", c.ID, "chat", GroupInput{BaseVersion: g.Version, AudienceID: g.AudienceID, SpaceIDs: g.SpaceIDs, Enabled: false})
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.Introspect(ctx, v.ID); !errors.Is(e, ErrDenied) {
		t.Fatal("disabled group active", e)
	}
}
