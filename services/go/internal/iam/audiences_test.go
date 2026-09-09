package iam

import (
	"context"
	"errors"
	"testing"
)

func TestChannelAudienceIsAdditionalReadConstraintNotUserMembership(t *testing.T) {
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
	if err = s.SetGrant(ctx, "admin", space.ID, "", "read", []string{"user:admin", "user:alice"}); err != nil {
		t.Fatal(err)
	}
	audience, err := s.CreateAudience(ctx, "admin", ChannelAudience{ID: "audience-1", ChannelID: "channel-1", GroupKey: "group-1", SpaceIDs: []string{space.ID}})
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"shared", "private"} {
		if err = s.RegisterResource(ctx, space.ID, id); err != nil {
			t.Fatal(err)
		}
	}
	if err = s.SetGrant(ctx, "admin", space.ID, "private", "read", []string{"user:alice"}); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		actor, resource string
		allowed         bool
	}{
		{"alice", "shared", true}, {"alice", "private", false}, {"bob", "shared", false},
	} {
		d, err := s.CheckChannel(ctx, tc.actor, audience.ID, audience.ChannelID, audience.GroupKey, "read", space.ID, tc.resource)
		if err != nil || d.Allowed != tc.allowed {
			t.Fatalf("%+v got %+v %v", tc, d, err)
		}
	}
	// Group availability must not become an additional OR subject in a normal user.
	principal, err := s.Principal(ctx, "bob")
	if err != nil || len(principal.Subjects) != 1 || principal.Subjects[0] != "user:bob" {
		t.Fatalf("membership broadened: %+v %v", principal, err)
	}
	personal, err := s.Check(ctx, "alice", "read", space.ID, "private")
	if err != nil || !personal.Allowed {
		t.Fatal("personal read unexpectedly removed")
	}
	for _, action := range []string{"grant", "review"} {
		d, err := s.CheckChannel(ctx, "admin", audience.ID, audience.ChannelID, audience.GroupKey, action, space.ID, "shared")
		if err != nil || d.Allowed {
			t.Fatalf("group delegation permits %s: %+v %v", action, d, err)
		}
	}
	for _, pair := range [][2]string{{"other-channel", audience.GroupKey}, {audience.ChannelID, "other-group"}} {
		d, err := s.CheckChannel(ctx, "alice", audience.ID, pair[0], pair[1], "read", space.ID, "shared")
		if err != nil || d.Allowed {
			t.Fatalf("audience reused across group/channel: %+v %v", d, err)
		}
	}
}

func TestAudienceRegistrationRequiresCurrentReadAndGrantAndIsIdempotent(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	for _, id := range []string{"admin", "editor"} {
		if _, err := s.Ensure(ctx, id, id, ""); err != nil {
			t.Fatal(err)
		}
	}
	space, err := s.CreateSpace(ctx, "admin", "Engineering")
	if err != nil {
		t.Fatal(err)
	}
	if err = s.SetGrant(ctx, "admin", space.ID, "", "read", []string{"user:admin", "user:editor"}); err != nil {
		t.Fatal(err)
	}
	input := ChannelAudience{ID: "audience-1", ChannelID: "channel-1", GroupKey: "group-1", SpaceIDs: []string{space.ID}}
	if _, err = s.CreateAudience(ctx, "editor", input); !errors.Is(err, ErrDenied) {
		t.Fatalf("read alone declassified group knowledge: %v", err)
	}
	first, err := s.CreateAudience(ctx, "admin", input)
	if err != nil {
		t.Fatal(err)
	}
	before, _ := s.Principal(ctx, "admin")
	retry, err := s.CreateAudience(ctx, "admin", input)
	if err != nil || retry.Version != first.Version {
		t.Fatalf("retry changed registration: %+v %v", retry, err)
	}
	after, _ := s.Principal(ctx, "admin")
	if after.AuthEpoch != before.AuthEpoch {
		t.Fatal("exact retry changed ACL epoch")
	}
	input.GroupKey = "another-group"
	if _, err = s.CreateAudience(ctx, "admin", input); !errors.Is(err, ErrConflict) {
		t.Fatalf("existing audience reassigned: %v", err)
	}
	if err = s.SetGrant(ctx, "admin", space.ID, "", "write", []string{"audience:" + first.ID}); !errors.Is(err, ErrInvalid) {
		t.Fatalf("audience received write authority: %v", err)
	}
}

func TestAudienceRevocationAndScopeChangesUseCASAndPreserveOtherGrants(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	for _, id := range []string{"admin", "alice"} {
		if _, err := s.Ensure(ctx, id, id, ""); err != nil {
			t.Fatal(err)
		}
	}
	first, _ := s.CreateSpace(ctx, "admin", "First")
	second, _ := s.CreateSpace(ctx, "admin", "Second")
	for _, space := range []string{first.ID, second.ID} {
		if err := s.SetGrant(ctx, "admin", space, "", "read", []string{"user:admin", "user:alice"}); err != nil {
			t.Fatal(err)
		}
	}
	a, err := s.CreateAudience(ctx, "admin", ChannelAudience{ID: "group-audience", ChannelID: "bot", GroupKey: "group", SpaceIDs: []string{first.ID}})
	if err != nil {
		t.Fatal(err)
	}
	v, err := s.VerifyAudience(ctx, a.ID, a.ChannelID, a.GroupKey, []string{first.ID})
	if err != nil || !v.Allowed || v.Version != a.Version {
		t.Fatalf("active audience not verifiable: %+v %v", v, err)
	}
	next, err := s.UpdateAudience(ctx, "admin", a.ID, a.ChannelID, a.GroupKey, a.Version, []string{second.ID}, true)
	if err != nil || next.Version != a.Version+1 {
		t.Fatalf("scope update: %+v %v", next, err)
	}
	if _, err := s.UpdateAudience(ctx, "admin", a.ID, a.ChannelID, a.GroupKey, a.Version, []string{first.ID}, true); !errors.Is(err, ErrConflict) {
		t.Fatalf("stale scope write: %v", err)
	}
	for _, tc := range []struct {
		space   string
		allowed bool
	}{{first.ID, false}, {second.ID, true}} {
		d, err := s.CheckChannel(ctx, "alice", a.ID, a.ChannelID, a.GroupKey, "read", tc.space, "")
		if err != nil || d.Allowed != tc.allowed {
			t.Fatalf("scope %s: %+v %v", tc.space, d, err)
		}
		personal, err := s.Check(ctx, "alice", "read", tc.space, "")
		if err != nil || !personal.Allowed {
			t.Fatalf("ordinary user grant changed: %+v %v", personal, err)
		}
	}
	stopped, err := s.UpdateAudience(ctx, "admin", a.ID, a.ChannelID, a.GroupKey, next.Version, []string{second.ID}, false)
	if err != nil || stopped.Active {
		t.Fatalf("disable: %+v %v", stopped, err)
	}
	v, err = s.VerifyAudience(ctx, a.ID, a.ChannelID, a.GroupKey, []string{second.ID})
	if err != nil || v.Allowed || v.AuthEpoch <= 0 {
		t.Fatalf("disabled audience verified: %+v %v", v, err)
	}
	if err := s.SetGrant(ctx, "admin", second.ID, "", "read", []string{"audience:" + a.ID}); !errors.Is(err, ErrInvalid) {
		t.Fatalf("inactive audience grant accepted: %v", err)
	}
}
