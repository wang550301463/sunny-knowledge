package iam

import (
	"context"
	"errors"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"strings"
)

var ErrConflict = errors.New("account version or identity conflict")
var ErrNotFound = errors.New("account unavailable")

type AccountInput struct{ ID, Name, Email, Kind, ClientID string }

func scanAccount(row pgx.Row) (u User, err error) {
	err = row.Scan(&u.ID, &u.Name, &u.Email, &u.Active, &u.Permissions, &u.Kind, &u.ClientID, &u.Version)
	if errors.Is(err, pgx.ErrNoRows) {
		err = ErrNotFound
	}
	return
}

const accountColumns = "id,name,email,active,permissions,account_kind,client_id,version"

func (s *Store) CreateAccount(ctx context.Context, actor string, in AccountInput) (account User, err error) {
	if !validID(in.ID) || !validName(in.Name) || len(in.Email) > 320 || (in.Kind != "user" && in.Kind != "service") || (in.Kind == "user" && in.ClientID != "") || (in.Kind == "service" && (!validID(in.ClientID) || strings.TrimSpace(in.ClientID) != in.ClientID)) {
		return account, ErrInvalid
	}
	account = User{ID: in.ID, Name: in.Name, Email: in.Email, Active: true, Permissions: []string{}, Kind: in.Kind, ClientID: in.ClientID, Version: 1}
	err = s.mutate(ctx, actor, "account.create", in.ID, account, func(tx pgx.Tx, n int64) error {
		if err := requireAdmin(ctx, tx, actor); err != nil {
			return err
		}
		_, err := tx.Exec(ctx, "INSERT INTO iam_users(id,name,email,account_kind,client_id) VALUES($1,$2,$3,$4,$5)", in.ID, in.Name, in.Email, in.Kind, in.ClientID)
		var constraint *pgconn.PgError
		if errors.As(err, &constraint) && constraint.Code == "23505" {
			return ErrConflict
		}
		return err
	})
	return
}
func (s *Store) Account(ctx context.Context, actor, id, kind string) (account User, err error) {
	err = s.read(ctx, func(tx pgx.Tx) error {
		if err := requireAdmin(ctx, tx, actor); err != nil {
			return err
		}
		account, err = scanAccount(tx.QueryRow(ctx, "SELECT "+accountColumns+" FROM iam_users WHERE id=$1 AND account_kind=$2 AND deleted_at IS NULL", id, kind))
		return err
	})
	return
}
func (s *Store) Accounts(ctx context.Context, actor, kind string) (items []User, err error) {
	items = []User{}
	err = s.read(ctx, func(tx pgx.Tx) error {
		if err := requireAdmin(ctx, tx, actor); err != nil {
			return err
		}
		rows, err := tx.Query(ctx, "SELECT "+accountColumns+" FROM iam_users WHERE account_kind=$1 AND deleted_at IS NULL ORDER BY name,id", kind)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			u, err := scanAccount(rows)
			if err != nil {
				return err
			}
			items = append(items, u)
		}
		return rows.Err()
	})
	return
}
func (s *Store) UpdateServiceAccount(ctx context.Context, actor, id, name string, active bool, baseVersion int64) (account User, err error) {
	if !validName(name) || baseVersion < 1 {
		return account, ErrInvalid
	}
	err = s.mutate(ctx, actor, "service_account.update", id, map[string]any{"name": name, "active": active, "base_version": baseVersion}, func(tx pgx.Tx, n int64) error {
		if err := requireAdmin(ctx, tx, actor); err != nil {
			return err
		}
		account, err = scanAccount(tx.QueryRow(ctx, "UPDATE iam_users SET name=$2,active=$3,version=version+1 WHERE id=$1 AND account_kind='service' AND deleted_at IS NULL AND version=$4 RETURNING "+accountColumns, id, name, active, baseVersion))
		if errors.Is(err, ErrNotFound) {
			return ErrConflict
		}
		return err
	})
	return
}
func (s *Store) DeleteServiceAccount(ctx context.Context, actor, id string, baseVersion int64) error {
	if baseVersion < 1 {
		return ErrInvalid
	}
	return s.mutate(ctx, actor, "service_account.delete", id, map[string]any{"base_version": baseVersion}, func(tx pgx.Tx, n int64) error {
		if err := requireAdmin(ctx, tx, actor); err != nil {
			return err
		}
		result, err := tx.Exec(ctx, "UPDATE iam_users SET active=false,deleted_at=now(),version=version+1 WHERE id=$1 AND account_kind='service' AND deleted_at IS NULL AND version=$2", id, baseVersion)
		if err != nil {
			return err
		}
		if result.RowsAffected() != 1 {
			return ErrConflict
		}
		return nil
	})
}
