package iam

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"strings"
)

//go:embed schema.sql
var schema string
var ErrDenied = errors.New("access denied")
var ErrInvalid = errors.New("invalid input")

var errNoChange = errors.New("no change")

type Store struct {
	Pool      *pgxpool.Pool
	Bootstrap string
}
type Space struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}
type Group struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Kind string `json:"kind"`
}
type User struct {
	ID          string   `json:"id"`
	Name        string   `json:"name"`
	Email       string   `json:"email"`
	Active      bool     `json:"active"`
	Permissions []string `json:"permissions"`
	Kind        string   `json:"account_kind"`
	ClientID    string   `json:"client_id"`
	Version     int64    `json:"version"`
}
type Policy struct {
	SpaceID              string   `json:"space_id"`
	ResourceID           string   `json:"resource_id"`
	SpaceReadSubjects    []string `json:"space_read_subjects"`
	ResourceReadSubjects []string `json:"resource_read_subjects"`
	ACLVersion           int64    `json:"acl_version"`
	ACLDomain            string   `json:"acl_domain"`
	AuthEpoch            int64    `json:"auth_epoch"`
}

func NewStore(p *pgxpool.Pool, b string) *Store { return &Store{p, b} }
func (s *Store) Migrate(ctx context.Context) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(728401)"); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, schema); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func validID(id string) bool {
	return id != "" && len(id) <= 256 && !strings.ContainsAny(id, "/\x00\r\n")
}
func validName(name string) bool { return strings.TrimSpace(name) != "" && len(name) <= 200 }
func validAction(action string) bool {
	return action == "read" || action == "write" || action == "grant" || action == "review"
}
func epoch(ctx context.Context, tx pgx.Tx) (int64, error) {
	var n int64
	err := tx.QueryRow(ctx, "SELECT value FROM iam_epoch WHERE id=1").Scan(&n)
	return n, err
}
func principal(ctx context.Context, tx pgx.Tx, id string) (platform.Principal, error) {
	p := platform.Principal{ID: id, Subjects: []string{"user:" + id}, Permissions: []string{}}
	var active bool
	if err := tx.QueryRow(ctx, "SELECT active,permissions FROM iam_users WHERE id=$1", id).Scan(&active, &p.Permissions); err != nil {
		return p, ErrDenied
	}
	if !active {
		return p, ErrDenied
	}
	rows, err := tx.Query(ctx, "SELECT g.kind,g.id FROM iam_memberships m JOIN iam_groups g ON g.id=m.group_id WHERE m.user_id=$1 ORDER BY g.kind,g.id", id)
	if err != nil {
		return p, err
	}
	defer rows.Close()
	for rows.Next() {
		var kind, id string
		if err := rows.Scan(&kind, &id); err != nil {
			return p, err
		}
		p.Subjects = append(p.Subjects, kind+":"+id)
	}
	if err = rows.Err(); err != nil {
		return p, err
	}
	p.AuthEpoch, err = epoch(ctx, tx)
	return p, err
}
func (s *Store) read(ctx context.Context, fn func(pgx.Tx) error) error {
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = fn(tx); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func (s *Store) mutate(ctx context.Context, actor, action, target string, detail any, fn func(pgx.Tx, int64) error) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	var n int64
	if err = tx.QueryRow(ctx, "UPDATE iam_epoch SET value=value+1 WHERE id=1 RETURNING value").Scan(&n); err != nil {
		return err
	}
	if err = fn(tx, n); err != nil {
		return err
	}
	data, err := json.Marshal(detail)
	if err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO iam_audit(actor,action,target,detail,auth_epoch) VALUES($1,$2,$3,$4,$5)", actor, action, target, data, n); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func requireAdmin(ctx context.Context, tx pgx.Tx, actor string) error {
	p, err := principal(ctx, tx, actor)
	if err != nil {
		return err
	}
	if !platform.HasPermission(p, "platform_admin") {
		return ErrDenied
	}
	return nil
}
func (s *Store) Ensure(ctx context.Context, id, name, email string, verifiedClient ...string) (platform.Principal, error) {
	if !validID(id) || len(name) > 200 || len(email) > 320 || len(verifiedClient) > 1 {
		return platform.Principal{}, ErrInvalid
	}
	if name == "" {
		name = id
	}
	azp := ""
	if len(verifiedClient) == 1 {
		azp = verifiedClient[0]
	}
	var exists bool
	if err := s.Pool.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM iam_users WHERE id=$1)", id).Scan(&exists); err != nil {
		return platform.Principal{}, err
	}
	if !exists {
		err := s.mutate(ctx, id, "user.ensure", id, map[string]string{"id": id}, func(tx pgx.Tx, n int64) error {
			permissions := []string{}
			if s.Bootstrap != "" && id == s.Bootstrap {
				permissions = []string{"platform_admin"}
			}
			result, err := tx.Exec(ctx, "INSERT INTO iam_users(id,name,email,permissions) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING", id, name, email, permissions)
			if err != nil {
				return err
			}
			if result.RowsAffected() == 0 {
				return errNoChange
			}
			return nil
		})
		if err != nil {
			return platform.Principal{}, err
		}
	}
	var p platform.Principal
	err := s.read(ctx, func(tx pgx.Tx) error {
		var kind, client string
		if err := tx.QueryRow(ctx, "SELECT account_kind,client_id FROM iam_users WHERE id=$1", id).Scan(&kind, &client); err != nil {
			return err
		}
		if kind == "service" && (azp == "" || azp != client) {
			return ErrDenied
		}
		var err error
		p, err = principal(ctx, tx, id)
		return err
	})
	return p, err
}
func (s *Store) Principal(ctx context.Context, id string) (p platform.Principal, err error) {
	err = s.read(ctx, func(tx pgx.Tx) error { p, err = principal(ctx, tx, id); return err })
	return
}
func (s *Store) CreateSpace(ctx context.Context, actor, name string) (space Space, err error) {
	if !validName(name) {
		return space, ErrInvalid
	}
	space = Space{platform.ID(), name}
	err = s.mutate(ctx, actor, "space.create", space.ID, space, func(tx pgx.Tx, n int64) error {
		if err := requireAdmin(ctx, tx, actor); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "INSERT INTO iam_spaces(id,name,created_by) VALUES($1,$2,$3)", space.ID, name, actor); err != nil {
			return err
		}
		for _, action := range []string{"read", "write", "grant", "review"} {
			if _, err := tx.Exec(ctx, "INSERT INTO iam_policies(space_id,action,subjects,version) VALUES($1,$2,$3,$4)", space.ID, action, []string{"user:" + actor}, n); err != nil {
				return err
			}
		}
		return nil
	})
	return
}
func (s *Store) CreateGroup(ctx context.Context, actor, kind, name string) (g Group, err error) {
	if !validName(name) || (kind != "group" && kind != "department") {
		return g, ErrInvalid
	}
	g = Group{platform.ID(), name, kind}
	err = s.mutate(ctx, actor, kind+".create", g.ID, g, func(tx pgx.Tx, n int64) error {
		if err := requireAdmin(ctx, tx, actor); err != nil {
			return err
		}
		_, err := tx.Exec(ctx, "INSERT INTO iam_groups(id,name,kind) VALUES($1,$2,$3)", g.ID, name, kind)
		return err
	})
	return
}
func (s *Store) SetMember(ctx context.Context, actor, group, user string, member bool) error {
	return s.mutate(ctx, actor, "membership.set", group, map[string]any{"user_id": user, "member": member}, func(tx pgx.Tx, n int64) error {
		if err := requireAdmin(ctx, tx, actor); err != nil {
			return err
		}
		var exists bool
		if err := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM iam_users WHERE id=$1)", user).Scan(&exists); err != nil {
			return err
		}
		if !exists {
			return ErrInvalid
		}
		var err error
		if member {
			_, err = tx.Exec(ctx, "INSERT INTO iam_memberships(group_id,user_id) VALUES($1,$2) ON CONFLICT DO NOTHING", group, user)
		} else {
			_, err = tx.Exec(ctx, "DELETE FROM iam_memberships WHERE group_id=$1 AND user_id=$2", group, user)
		}
		return err
	})
}
func (s *Store) SetUser(ctx context.Context, actor, id, name string, active bool) error {
	if !validName(name) {
		return ErrInvalid
	}
	return s.mutate(ctx, actor, "user.update", id, map[string]any{"name": name, "active": active}, func(tx pgx.Tx, n int64) error {
		if err := requireAdmin(ctx, tx, actor); err != nil {
			return err
		}
		result, err := tx.Exec(ctx, "UPDATE iam_users SET name=$2,active=$3 WHERE id=$1", id, name, active)
		if err == nil && result.RowsAffected() != 1 {
			return ErrInvalid
		}
		return err
	})
}
func (s *Store) RegisterResource(ctx context.Context, space, id string) error {
	if !validID(space) || !validID(id) {
		return ErrInvalid
	}
	_, err := s.Pool.Exec(ctx, "INSERT INTO iam_resources(space_id,id) VALUES($1,$2) ON CONFLICT DO NOTHING", space, id)
	return err
}

// RegisterSourceResource establishes an inherited ACL before source preview.
// The actor is resolved by auth; authorization and registration share the mutation
// transaction so revocation cannot race an earlier, separately committed check.
func (s *Store) RegisterSourceResource(ctx context.Context, actor, space, id string) error {
	if !validID(space) || !validID(id) || !strings.HasPrefix(id, "source:") || len(id) <= len("source:") {
		return ErrInvalid
	}
	return s.mutate(ctx, actor, "source_resource.register", space+"/"+id, map[string]string{"space_id": space, "resource_id": id, "caller": "ingest"}, func(tx pgx.Tx, n int64) error {
		var exists bool
		if err := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM iam_resources WHERE space_id=$1 AND id=$2)", space, id).Scan(&exists); err != nil {
			return err
		}
		resource := ""
		if exists {
			resource = id
		}
		for _, action := range []string{"read", "write"} {
			decision, err := check(ctx, tx, actor, action, space, resource)
			if err != nil {
				return err
			}
			if !decision.Allowed {
				return ErrDenied
			}
		}
		if exists {
			return errNoChange
		}
		_, err := tx.Exec(ctx, "INSERT INTO iam_resources(space_id,id) VALUES($1,$2)", space, id)
		return err
	})
}
func readSubjects(ctx context.Context, tx pgx.Tx, space, resource, action string) ([]string, int64, error) {
	var subjects []string
	var version int64
	err := tx.QueryRow(ctx, "SELECT subjects,version FROM iam_policies WHERE space_id=$1 AND resource_id=$2 AND action=$3", space, resource, action).Scan(&subjects, &version)
	if errors.Is(err, pgx.ErrNoRows) && resource != "" {
		return nil, 0, nil
	}
	return subjects, version, err
}
func policy(ctx context.Context, tx pgx.Tx, space, resource string) (p Policy, err error) {
	p = Policy{SpaceID: space, ResourceID: resource}
	var a, b int64
	p.SpaceReadSubjects, a, err = readSubjects(ctx, tx, space, "", "read")
	if err != nil {
		return p, ErrDenied
	}
	if resource != "" {
		p.ResourceReadSubjects, b, err = readSubjects(ctx, tx, space, resource, "read")
		if err != nil {
			return p, err
		}
	}
	p.ACLVersion = max(a, b)
	p.ACLDomain = Domain(space, p.SpaceReadSubjects, p.ResourceReadSubjects)
	p.AuthEpoch, err = epoch(ctx, tx)
	return
}
func check(ctx context.Context, tx pgx.Tx, id, action, space, resource string) (d platform.Decision, err error) {
	if !validAction(action) || !validID(space) {
		return d, ErrInvalid
	}
	p, err := principal(ctx, tx, id)
	if err != nil {
		return d, err
	}
	pol, err := policy(ctx, tx, space, resource)
	if err != nil {
		return d, err
	}
	spaceACL, a, err := readSubjects(ctx, tx, space, "", action)
	if err != nil {
		return d, err
	}
	var resourceACL []string
	var b int64
	if resource != "" {
		resourceACL, b, err = readSubjects(ctx, tx, space, resource, action)
		if err != nil {
			return d, err
		}
	}
	return platform.Decision{Allowed: Allowed(p.Subjects, spaceACL, resourceACL), AuthEpoch: p.AuthEpoch, ACLDomain: pol.ACLDomain, ACLVersion: max(pol.ACLVersion, a, b)}, nil
}
func (s *Store) Check(ctx context.Context, id, action, space, resource string) (d platform.Decision, err error) {
	err = s.read(ctx, func(tx pgx.Tx) error { d, err = check(ctx, tx, id, action, space, resource); return err })
	return
}
func (s *Store) Policy(ctx context.Context, space, resource string) (p Policy, err error) {
	err = s.read(ctx, func(tx pgx.Tx) error { p, err = policy(ctx, tx, space, resource); return err })
	return
}
func (s *Store) SetGrant(ctx context.Context, actor, space, resource, action string, subjects []string) error {
	if !validAction(action) || !validID(space) || (resource != "" && !validID(resource)) || (resource == "" && subjects == nil) || len(subjects) > 10000 {
		return ErrInvalid
	}
	subjects = Canonical(subjects)
	return s.mutate(ctx, actor, "grant.replace", space+"/"+resource, map[string]any{"action": action, "subjects": subjects}, func(tx pgx.Tx, n int64) error {
		d, err := check(ctx, tx, actor, "grant", space, resource)
		if err != nil {
			return err
		}
		if !d.Allowed {
			return ErrDenied
		}
		for _, subject := range subjects {
			kind, id, ok := strings.Cut(subject, ":")
			if !ok || !validID(id) {
				return ErrInvalid
			}
			var exists bool
			switch kind {
			case "user":
				err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM iam_users WHERE id=$1)", id).Scan(&exists)
			case "group", "department":
				err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM iam_groups WHERE id=$1 AND kind=$2)", id, kind).Scan(&exists)
			default:
				return ErrInvalid
			}
			if err != nil {
				return err
			}
			if !exists {
				return fmt.Errorf("%w: unknown subject", ErrInvalid)
			}
		}
		if resource != "" {
			var exists bool
			if err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM iam_resources WHERE space_id=$1 AND id=$2)", space, resource).Scan(&exists); err != nil {
				return err
			}
			if !exists {
				return ErrInvalid
			}
		}
		_, err = tx.Exec(ctx, "INSERT INTO iam_policies(space_id,resource_id,action,subjects,version) VALUES($1,$2,$3,$4,$5) ON CONFLICT(space_id,resource_id,action) DO UPDATE SET subjects=excluded.subjects,version=excluded.version", space, resource, action, subjects, n)
		return err
	})
}

// AuthorizedGrants returns only policies from the same snapshot as the grant decision.
func (s *Store) AuthorizedGrants(ctx context.Context, actor, space, resource string) (items []Grant, err error) {
	items = []Grant{}
	err = s.read(ctx, func(tx pgx.Tx) error {
		decision, err := check(ctx, tx, actor, "grant", space, resource)
		if err != nil {
			return err
		}
		if !decision.Allowed {
			return ErrDenied
		}
		rows, err := tx.Query(ctx, "SELECT space_id,resource_id,action,subjects,version FROM iam_policies WHERE space_id=$1 AND resource_id=$2 ORDER BY action", space, resource)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var grant Grant
			if err := rows.Scan(&grant.SpaceID, &grant.ResourceID, &grant.Action, &grant.Subjects, &grant.Version); err != nil {
				return err
			}
			items = append(items, grant)
		}
		return rows.Err()
	})
	return
}
