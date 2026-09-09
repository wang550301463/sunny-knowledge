package channel

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"errors"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"net/http/httptest"
	"testing"
	"time"
)

func historicalFixture(t *testing.T) (*Store, Config, Lease, RunContext) {
	t.Helper()
	s := integrationStore(t)
	c := enabled(t, s)
	reviewBoundUser(t, s, c)
	ctx := context.Background()
	l, err := s.Acquire(ctx, c.ID, "worker", time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	m := Message{ID: "message", RequestID: "request", UserID: "external-user", ChatType: "single", Text: "original question"}
	if _, err = s.Accept(ctx, l, m); err != nil {
		t.Fatal(err)
	}
	v, err := s.CreateContext(ctx, l, m)
	if err != nil {
		t.Fatal(err)
	}
	if err = s.RecordRun(ctx, l, m.ID, "run"); err != nil {
		t.Fatal(err)
	}
	return s, c, l, v
}

func TestHistoricalContextHTTPAndGroupChangeRevalidateLiveIsolation(t *testing.T) {
	s, c, l, _ := historicalFixture(t)
	ctx := context.Background()
	g, err := saveTestGroup(s, ctx, "admin", c.ID, "chat", GroupInput{AudienceID: "audience", SpaceIDs: []string{"s"}, Enabled: true})
	if err != nil {
		t.Fatal(err)
	}
	m := Message{ID: "group-message", RequestID: "group-request", UserID: "external-user", ChatType: "group", ChatID: "chat", Text: "group question"}
	if _, err = s.Accept(ctx, l, m); err != nil {
		t.Fatal(err)
	}
	if _, err = s.CreateContext(ctx, l, m); err != nil {
		t.Fatal(err)
	}
	if err = s.RecordRun(ctx, l, m.ID, "group-run"); err != nil {
		t.Fatal(err)
	}
	pub, key, _ := ed25519.GenerateKey(rand.Reader)
	sec := platform.NewServiceSecurity(key, map[string]ed25519.PublicKey{"channel": pub, "auth": pub, "gateway": pub, "agent": pub})
	h := NewHandler(s, platform.NewClient("channel", sec), "", sec, nil)
	call := func(who string, want int) {
		t.Helper()
		r := httptest.NewRequest("GET", "/internal/v1/runs/group-run/context", nil)
		if who != "" {
			token, _ := sec.Mint(who, "channel")
			r.Header.Set("X-Service-Token", token)
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != want {
			t.Fatalf("history caller %s status %d want %d", who, w.Code, want)
		}
		if want == 200 && w.Header().Get("Cache-Control") != "no-store" {
			t.Fatal("cacheable history context")
		}
	}
	call("", 401)
	call("gateway", 403)
	call("agent", 403)
	call("auth", 200)
	// Preparing an unconfirmed group scope change immediately hides old history.
	if _, err = s.PrepareGroup(ctx, "admin", c.ID, "chat", GroupInput{BaseVersion: g.Version, AudienceID: g.AudienceID, SpaceIDs: g.SpaceIDs, Enabled: false}); err != nil {
		t.Fatal(err)
	}
	call("auth", 403)
	if _, err = s.HistoricalContext(ctx, "run"); err != nil {
		t.Fatal("private conversation affected by a different group", err)
	}
}

func TestHistoricalContextSurvivesMessageExpiryAndWorkerReplacement(t *testing.T) {
	s, c, l, v := historicalFixture(t)
	ctx := context.Background()
	v.ExpiresAt = time.Now().Add(-time.Hour)
	if _, err := s.Pool.Exec(ctx, "UPDATE channel_contexts SET expires_at=$2,payload=$3 WHERE id=$1", v.ID, v.ExpiresAt, encode(v)); err != nil {
		t.Fatal(err)
	}
	if err := s.Release(ctx, l); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Acquire(ctx, c.ID, "replacement", time.Minute); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Introspect(ctx, v.ID); !errors.Is(err, ErrDenied) {
		t.Fatal("expired message became executable", err)
	}
	out, err := s.HistoricalContext(ctx, "run")
	if err != nil || out.RunID != "run" || out.Context.ID != v.ID || out.Context.UserID != "alice" || !out.Context.ExpiresAt.Equal(v.ExpiresAt) {
		t.Fatal("read-only history lost stable binding", err)
	}
	if err = s.Unbind(ctx, "alice", c.ID, "external-user"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.HistoricalContext(ctx, "run"); !errors.Is(err, ErrDenied) {
		t.Fatal("unbound history exposed", err)
	}
}

func TestHistoricalContextRejectsMutableMappingsAndClearedConversation(t *testing.T) {
	s, _, l, v := historicalFixture(t)
	ctx := context.Background()
	if err := s.RecordRun(ctx, l, "message", "different-run"); !errors.Is(err, ErrConflict) {
		t.Fatal("run mapping replaced", err)
	}
	if err := s.FinishMessage(ctx, l, "message", "sent", "different-run"); !errors.Is(err, ErrConflict) {
		t.Fatal("completion replaced mapping", err)
	}
	if err := s.FinishMessage(ctx, l, "message", "sent", ""); err != nil {
		t.Fatal(err)
	}
	if _, err := s.HistoricalContext(ctx, "run"); err != nil {
		t.Fatal("completion erased mapping", err)
	}
	if err := s.ClearConversation(ctx, v); err != nil {
		t.Fatal(err)
	}
	if _, err := s.HistoricalContext(ctx, "run"); !errors.Is(err, ErrDenied) {
		t.Fatal("cleared conversation history exposed", err)
	}
}

func TestHistoricalContextRejectsAmbiguousRecordsAndStaleWorkerWrites(t *testing.T) {
	s, c, l, v := historicalFixture(t)
	ctx := context.Background()
	duplicate := v
	duplicate.ID = "duplicate-context"
	raw, _ := json.Marshal(duplicate)
	if _, err := s.Pool.Exec(ctx, "INSERT INTO channel_contexts(id,channel_id,message_id,owner,fence,payload,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7)", duplicate.ID, c.ID, v.MessageID, l.Owner, l.Fence, raw, v.ExpiresAt); err != nil {
		t.Fatal(err)
	}
	if _, err := s.HistoricalContext(ctx, "run"); !errors.Is(err, ErrDenied) {
		t.Fatal("ambiguous context accepted", err)
	}
	if err := s.Release(ctx, l); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Acquire(ctx, c.ID, "replacement", time.Minute); err != nil {
		t.Fatal(err)
	}
	if err := s.RecordRun(ctx, l, "message", "run"); !errors.Is(err, ErrLeaseLost) {
		t.Fatal("old worker modified run binding", err)
	}
	if err := s.FinishMessage(ctx, l, "message", "failed", "run"); !errors.Is(err, ErrLeaseLost) {
		t.Fatal("old worker modified message outcome", err)
	}
}

func reviewBoundUser(t *testing.T, s *Store, c Config) {
	t.Helper()
	ctx := context.Background()
	l, e := s.Acquire(ctx, c.ID, "binding-owner", time.Minute)
	if e != nil {
		t.Fatal(e)
	}
	ch, e := s.BeginChallenge(ctx, l, "external-user")
	if e != nil {
		t.Fatal(e)
	}
	proof, e := s.ClaimChallenge(ctx, "alice", ch.ID, ch.WebToken)
	if e != nil {
		t.Fatal(e)
	}
	if e = s.ConfirmChallenge(ctx, l, "external-user", ch.ID, proof); e != nil {
		t.Fatal(e)
	}
	if e = s.Release(ctx, l); e != nil {
		t.Fatal(e)
	}
}

func saveTestGroup(s *Store, ctx context.Context, actor, channel, chat string, in GroupInput) (Group, error) {
	return s.SaveGroup(ctx, actor, channel, chat, in)
}
