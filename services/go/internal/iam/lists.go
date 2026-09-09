package iam

import (
	"context"
	"encoding/json"
	"github.com/jackc/pgx/v5"
	"time"
)

type jsonSubjects struct {
	Present bool
	Value   []string
}

func (j *jsonSubjects) UnmarshalJSON(b []byte) error {
	j.Present = true
	return json.Unmarshal(b, &j.Value)
}

type Grant struct {
	SpaceID    string   `json:"space_id"`
	ResourceID string   `json:"resource_id"`
	Action     string   `json:"action"`
	Subjects   []string `json:"subjects"`
	Version    int64    `json:"version"`
}
type AuditEntry struct {
	ID         int64           `json:"id"`
	Actor      string          `json:"actor"`
	Action     string          `json:"action"`
	Target     string          `json:"target"`
	Detail     json.RawMessage `json:"detail"`
	AuthEpoch  int64           `json:"auth_epoch"`
	OccurredAt time.Time       `json:"occurred_at"`
}

func (s *Store) Spaces(ctx context.Context, actor string) (items []Space, err error) {
	items = []Space{}
	err = s.read(ctx, func(tx pgx.Tx) error {
		p, err := principal(ctx, tx, actor)
		if err != nil {
			return err
		}
		rows, err := tx.Query(ctx, "SELECT s.id,s.name,p.subjects FROM iam_spaces s JOIN iam_policies p ON p.space_id=s.id AND p.resource_id='' AND p.action='read' ORDER BY s.name,s.id")
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var v Space
			var subjects []string
			if err := rows.Scan(&v.ID, &v.Name, &subjects); err != nil {
				return err
			}
			if Allowed(p.Subjects, subjects, nil) {
				items = append(items, v)
			}
		}
		return rows.Err()
	})
	return
}
func (s *Store) Groups(ctx context.Context, actor, kind string) (items []Group, err error) {
	items = []Group{}
	err = s.read(ctx, func(tx pgx.Tx) error {
		if err := requireAdmin(ctx, tx, actor); err != nil {
			return err
		}
		rows, err := tx.Query(ctx, "SELECT id,name,kind FROM iam_groups WHERE kind=$1 ORDER BY name,id", kind)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var v Group
			if err := rows.Scan(&v.ID, &v.Name, &v.Kind); err != nil {
				return err
			}
			items = append(items, v)
		}
		return rows.Err()
	})
	return
}
func (s *Store) Users(ctx context.Context, actor string) (items []User, err error) {
	items = []User{}
	err = s.read(ctx, func(tx pgx.Tx) error {
		if err := requireAdmin(ctx, tx, actor); err != nil {
			return err
		}
		rows, err := tx.Query(ctx, "SELECT id,name,email,active,permissions FROM iam_users ORDER BY name,id")
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var v User
			if err := rows.Scan(&v.ID, &v.Name, &v.Email, &v.Active, &v.Permissions); err != nil {
				return err
			}
			items = append(items, v)
		}
		return rows.Err()
	})
	return
}
func (s *Store) Members(ctx context.Context, actor, group string) (items []User, err error) {
	items = []User{}
	err = s.read(ctx, func(tx pgx.Tx) error {
		if err := requireAdmin(ctx, tx, actor); err != nil {
			return err
		}
		rows, err := tx.Query(ctx, "SELECT u.id,u.name,u.email,u.active,u.permissions FROM iam_users u JOIN iam_memberships m ON m.user_id=u.id WHERE m.group_id=$1 ORDER BY u.name,u.id", group)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var v User
			if err := rows.Scan(&v.ID, &v.Name, &v.Email, &v.Active, &v.Permissions); err != nil {
				return err
			}
			items = append(items, v)
		}
		return rows.Err()
	})
	return
}
func (s *Store) Grants(ctx context.Context, space, resource string) (items []Grant, err error) {
	items = []Grant{}
	rows, err := s.Pool.Query(ctx, "SELECT space_id,resource_id,action,subjects,version FROM iam_policies WHERE space_id=$1 AND resource_id=$2 ORDER BY action", space, resource)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var v Grant
		if err := rows.Scan(&v.SpaceID, &v.ResourceID, &v.Action, &v.Subjects, &v.Version); err != nil {
			return nil, err
		}
		items = append(items, v)
	}
	return items, rows.Err()
}
func (s *Store) Audit(ctx context.Context, actor string) (items []AuditEntry, err error) {
	items = []AuditEntry{}
	err = s.read(ctx, func(tx pgx.Tx) error {
		if err := requireAdmin(ctx, tx, actor); err != nil {
			return err
		}
		rows, err := tx.Query(ctx, "SELECT id,actor,action,target,detail,auth_epoch,occurred_at FROM iam_audit ORDER BY id DESC LIMIT 200")
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var v AuditEntry
			if err := rows.Scan(&v.ID, &v.Actor, &v.Action, &v.Target, &v.Detail, &v.AuthEpoch, &v.OccurredAt); err != nil {
				return err
			}
			items = append(items, v)
		}
		return rows.Err()
	})
	return
}
