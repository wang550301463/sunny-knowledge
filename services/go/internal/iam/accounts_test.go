package iam

import (
	"context"
	"errors"
	"slices"
	"sync"
	"testing"
)

func TestPostgresPreprovisionKeepsExternalSubjectAndProfile(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	if _, err := s.Ensure(ctx, "admin", "Admin", ""); err != nil {
		t.Fatal(err)
	}
	registered, err := s.CreateAccount(ctx, "admin", AccountInput{ID: "known-kc-subject", Name: "Provisioned Name", Email: "person@example.test", Kind: "user"})
	if err != nil {
		t.Fatal(err)
	}
	if registered.Kind != "user" || registered.ClientID != "" || !registered.Active || registered.Version != 1 {
		t.Fatalf("invalid registered account: %+v", registered)
	}
	p, err := s.Principal(ctx, registered.ID)
	if err != nil || !slices.Contains(p.Subjects, "user:"+registered.ID) {
		t.Fatalf("pre-login membership unavailable %+v %v", p, err)
	}
	if _, err = s.Ensure(ctx, registered.ID, "Token Name", "different@example.test"); err != nil {
		t.Fatal(err)
	}
	current, err := s.Account(ctx, "admin", registered.ID, "user")
	if err != nil || current.Name != registered.Name || current.Email != registered.Email || current.Kind != "user" {
		t.Fatalf("login changed registered identity %+v %v", current, err)
	}
	if _, err = s.CreateAccount(ctx, "admin", AccountInput{ID: registered.ID, Name: "Conflicting Service", Kind: "service", ClientID: "mcp-client"}); !errors.Is(err, ErrConflict) {
		t.Fatalf("kind collision should conflict, got %v", err)
	}
	if _, err = s.CreateAccount(ctx, registered.ID, AccountInput{ID: "other", Name: "Other", Kind: "user"}); !errors.Is(err, ErrDenied) {
		t.Fatalf("non-admin provisioned user: %v", err)
	}
	var audits int
	if err = s.Pool.QueryRow(ctx, "SELECT count(*) FROM iam_audit WHERE action='account.create' AND target=$1", registered.ID).Scan(&audits); err != nil || audits != 1 {
		t.Fatalf("preprovision audit %d %v", audits, err)
	}
}

func TestPostgresServiceAccountClientBindingAndLiveACL(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	if _, err := s.Ensure(ctx, "admin", "Admin", ""); err != nil {
		t.Fatal(err)
	}
	account, err := s.CreateAccount(ctx, "admin", AccountInput{ID: "kc-service-sub", Name: "MCP integration", Kind: "service", ClientID: "mcp-client"})
	if err != nil {
		t.Fatal(err)
	}
	for _, client := range []string{"", "other-client"} {
		if _, err = s.Ensure(ctx, account.ID, "token-name", "", client); !errors.Is(err, ErrDenied) {
			t.Fatalf("azp %q accepted: %v", client, err)
		}
	}
	p, err := s.Ensure(ctx, account.ID, "token-name", "", "mcp-client")
	if err != nil || !slices.Contains(p.Subjects, "service:"+account.ID) || slices.Contains(p.Subjects, "user:"+account.ID) {
		t.Fatalf("wrong service subjects %+v %v", p, err)
	}
	space, err := s.CreateSpace(ctx, "admin", "Restricted")
	if err != nil {
		t.Fatal(err)
	}
	if d, err := s.Check(ctx, account.ID, "read", space.ID, ""); err != nil || d.Allowed {
		t.Fatalf("implicit service access %+v %v", d, err)
	}
	if err = s.SetGrant(ctx, "admin", space.ID, "", "read", []string{"user:" + account.ID}); !errors.Is(err, ErrInvalid) {
		t.Fatalf("incorrect user subject accepted: %v", err)
	}
	if err = s.SetGrant(ctx, "admin", space.ID, "", "read", []string{"service:" + account.ID}); err != nil {
		t.Fatal(err)
	}
	if d, err := s.Check(ctx, account.ID, "read", space.ID, ""); err != nil || !d.Allowed {
		t.Fatalf("explicit service grant failed %+v %v", d, err)
	}
	group, err := s.CreateGroup(ctx, "admin", "group", "MCP readers")
	if err != nil {
		t.Fatal(err)
	}
	if err = s.SetMember(ctx, "admin", group.ID, account.ID, true); err != nil {
		t.Fatal(err)
	}
	if err = s.SetGrant(ctx, "admin", space.ID, "", "read", []string{"group:" + group.ID}); err != nil {
		t.Fatal(err)
	}
	if d, err := s.Check(ctx, account.ID, "read", space.ID, ""); err != nil || !d.Allowed {
		t.Fatalf("service group grant failed %+v %v", d, err)
	}
	if err = s.SetMember(ctx, "admin", group.ID, account.ID, false); err != nil {
		t.Fatal(err)
	}
	if d, err := s.Check(ctx, account.ID, "read", space.ID, ""); err != nil || d.Allowed {
		t.Fatalf("service group revocation failed %+v %v", d, err)
	}
	updated, err := s.UpdateServiceAccount(ctx, "admin", account.ID, "MCP disabled", false, account.Version)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.Ensure(ctx, account.ID, "token-name", "", "mcp-client"); !errors.Is(err, ErrDenied) {
		t.Fatalf("disabled service token accepted: %v", err)
	}
	if _, err = s.UpdateServiceAccount(ctx, "admin", account.ID, "Stale update", true, account.Version); !errors.Is(err, ErrConflict) {
		t.Fatalf("stale CAS update accepted: %v", err)
	}
	if err = s.DeleteServiceAccount(ctx, "admin", account.ID, updated.Version); err != nil {
		t.Fatal(err)
	}
	if _, err = s.Ensure(ctx, account.ID, "token-name", "", "mcp-client"); !errors.Is(err, ErrDenied) {
		t.Fatalf("deleted service resurrected: %v", err)
	}
	if _, err = s.CreateAccount(ctx, "admin", AccountInput{ID: account.ID, Name: "User again", Kind: "user"}); !errors.Is(err, ErrConflict) {
		t.Fatalf("deleted service changed type: %v", err)
	}
}

func TestPostgresServiceAccountConcurrentUniqueRegistration(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	if _, err := s.Ensure(ctx, "admin", "Admin", ""); err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	results := make(chan error, 2)
	for _, id := range []string{"subject-a", "subject-b"} {
		wg.Add(1)
		go func(id string) {
			defer wg.Done()
			_, err := s.CreateAccount(ctx, "admin", AccountInput{ID: id, Name: id, Kind: "service", ClientID: "same-client"})
			results <- err
		}(id)
	}
	wg.Wait()
	close(results)
	success, conflict := 0, 0
	for err := range results {
		if err == nil {
			success++
		} else if errors.Is(err, ErrConflict) {
			conflict++
		} else {
			t.Fatal(err)
		}
	}
	if success != 1 || conflict != 1 {
		t.Fatalf("unique registration success=%d conflict=%d", success, conflict)
	}
}
