package channel

import (
	"context"
	"encoding/json"
	"errors"
	"slices"

	"github.com/jackc/pgx/v5"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

// AudienceSnapshot is the committed audience state as observed by the channel service.
type AudienceSnapshot struct {
	ID        string   `json:"id"`
	ChannelID string   `json:"channel_id"`
	GroupKey  string   `json:"group_key"`
	SpaceIDs  []string `json:"space_ids"`
	Active    bool     `json:"active"`
	Version   int64    `json:"version"`
	AuthEpoch int64    `json:"auth_epoch"`
}

const groupCols = "id,channel_id,chat_id,audience_id,space_ids,version,enabled,audience_version,desired_enabled,sync_state,sync_error,pending_operation_id"

func scanGroup(row pgx.Row) (Group, error) {
	var g Group
	e := row.Scan(&g.ID, &g.ChannelID, &g.ChatID, &g.AudienceID, &g.SpaceIDs, &g.Version, &g.Enabled, &g.AudienceVersion, &g.DesiredEnabled, &g.SyncState, &g.SyncError, &g.PendingOperationID)
	return g, mapped(e)
}
func (s *Store) GetGroup(ctx context.Context, channel, chat string) (Group, error) {
	return scanGroup(s.Pool.QueryRow(ctx, "SELECT "+groupCols+" FROM channel_groups WHERE channel_id=$1 AND chat_id=$2", channel, chat))
}
func (s *Store) ListGroups(ctx context.Context, channel string) ([]Group, error) {
	r, e := s.Pool.Query(ctx, "SELECT "+groupCols+" FROM channel_groups WHERE channel_id=$1 ORDER BY id LIMIT 100", channel)
	if e != nil {
		return nil, e
	}
	defer r.Close()
	out := []Group{}
	for r.Next() {
		g, e := scanGroup(r)
		if e != nil {
			return nil, e
		}
		out = append(out, g)
	}
	return out, r.Err()
}
func validGroupSpaces(ids []string) bool {
	if len(ids) < 1 || len(ids) > 100 {
		return false
	}
	seen := map[string]bool{}
	for _, id := range ids {
		if !key(id) || seen[id] {
			return false
		}
		seen[id] = true
	}
	return true
}
func sameSpaces(a, b []string) bool {
	a = slices.Clone(a)
	b = slices.Clone(b)
	slices.Sort(a)
	slices.Sort(b)
	return slices.Equal(a, b)
}
func desiredAudience(g Group) AudienceSnapshot {
	return AudienceSnapshot{ID: g.AudienceID, ChannelID: g.ChannelID, GroupKey: g.ID, SpaceIDs: g.SpaceIDs, Active: g.DesiredEnabled}
}
func sameAudienceState(a, b AudienceSnapshot) bool {
	return a.ID == b.ID && a.ChannelID == b.ChannelID && a.GroupKey == b.GroupKey && a.Active == b.Active && sameSpaces(a.SpaceIDs, b.SpaceIDs)
}
func (a AudienceSnapshot) valid() bool {
	return key(a.ID) && key(a.ChannelID) && key(a.GroupKey) && validGroupSpaces(a.SpaceIDs) && a.Version > 0 && a.AuthEpoch > 0
}

type audienceBaseline struct {
	Exists   bool             `json:"exists"`
	Snapshot AudienceSnapshot `json:"snapshot"`
}
type groupOperation struct {
	ID       string
	Group    Group
	Baseline *audienceBaseline
}

// This order matches sending: lease -> configuration -> group. The initial
// transaction commits the read block before any IAM network request can start.
func (s *Store) groupTransaction(ctx context.Context, channel, chat string, fn func(pgx.Tx, Group, []string) error) error {
	tx, e := s.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer tx.Rollback(ctx)
	if _, e = tx.Exec(ctx, "SELECT 1 FROM channel_leases WHERE channel_id=$1 FOR UPDATE", channel); e != nil {
		return e
	}
	var spaces []string
	if e = tx.QueryRow(ctx, "SELECT space_ids FROM channel_configs WHERE id=$1 FOR UPDATE", channel).Scan(&spaces); e != nil {
		return mapped(e)
	}
	g, e := scanGroup(tx.QueryRow(ctx, "SELECT "+groupCols+" FROM channel_groups WHERE channel_id=$1 AND chat_id=$2 FOR UPDATE", channel, chat))
	if e != nil && !errors.Is(e, ErrNotFound) {
		return e
	}
	if e = fn(tx, g, spaces); e != nil {
		return e
	}
	return tx.Commit(ctx)
}
func (s *Store) PrepareGroup(ctx context.Context, actor, channel, chat string, in GroupInput) (Group, error) {
	if !key(channel) || !key(chat) || !key(in.AudienceID) || !validGroupSpaces(in.SpaceIDs) || in.BaseVersion < 0 {
		return Group{}, ErrInvalid
	}
	if in.Enabled && !in.AcknowledgedPublicToGroup {
		return Group{}, ErrDenied
	}
	in.SpaceIDs = slices.Clone(in.SpaceIDs)
	slices.Sort(in.SpaceIDs)
	var out Group
	e := s.groupTransaction(ctx, channel, chat, func(tx pgx.Tx, g Group, spaces []string) error {
		if in.Enabled && len(intersect(in.SpaceIDs, spaces)) != len(in.SpaceIDs) {
			return ErrInvalid
		}
		if g.ID == "" {
			if in.BaseVersion != 0 {
				return ErrConflict
			}
			g = Group{ID: platform.ID(), ChannelID: channel, ChatID: chat, AudienceID: in.AudienceID}
		}
		if g.Version != in.BaseVersion || g.AudienceID != in.AudienceID {
			return ErrConflict
		}
		if g.PendingOperationID != "" {
			if _, e := tx.Exec(ctx, "UPDATE channel_group_operations SET state='superseded' WHERE id=$1 AND state='pending'", g.PendingOperationID); e != nil {
				return e
			}
		}
		g.Version++
		g.SpaceIDs = in.SpaceIDs
		g.Enabled = false
		g.DesiredEnabled = in.Enabled
		g.SyncState = "pending"
		g.SyncError = ""
		g.PendingOperationID = platform.ID()
		if in.BaseVersion == 0 {
			_, e := tx.Exec(ctx, "INSERT INTO channel_groups(id,channel_id,chat_id,audience_id,space_ids,version,enabled,audience_version,desired_enabled,sync_state,pending_operation_id) VALUES($1,$2,$3,$4,$5,$6,false,$7,$8,'pending',$9)", g.ID, channel, chat, g.AudienceID, g.SpaceIDs, g.Version, g.AudienceVersion, g.DesiredEnabled, g.PendingOperationID)
			if e != nil {
				return mapped(e)
			}
		} else {
			_, e := tx.Exec(ctx, "UPDATE channel_groups SET space_ids=$2,version=$3,enabled=false,desired_enabled=$4,sync_state='pending',sync_error='',pending_operation_id=$5 WHERE id=$1", g.ID, g.SpaceIDs, g.Version, g.DesiredEnabled, g.PendingOperationID)
			if e != nil {
				return e
			}
		}
		if _, e := tx.Exec(ctx, "INSERT INTO channel_group_operations(id,group_id,base_version,target_version,intent,created_by) VALUES($1,$2,$3,$4,$5,$6)", g.PendingOperationID, g.ID, in.BaseVersion, g.Version, encode(in), actor); e != nil {
			return e
		}
		_, e := tx.Exec(ctx, "INSERT INTO channel_audit(actor,action,target) VALUES($1,'group.prepared',$2)", actor, g.ID)
		out = g
		return e
	})
	return out, e
}
func (s *Store) pendingGroup(ctx context.Context, channel, chat string, version int64) (groupOperation, error) {
	g, e := s.GetGroup(ctx, channel, chat)
	if e != nil {
		return groupOperation{}, e
	}
	if g.Version != version || g.SyncState != "pending" || g.PendingOperationID == "" {
		return groupOperation{}, ErrConflict
	}
	var raw []byte
	var state string
	var target int64
	e = s.Pool.QueryRow(ctx, "SELECT remote_baseline,state,target_version FROM channel_group_operations WHERE id=$1 AND group_id=$2", g.PendingOperationID, g.ID).Scan(&raw, &state, &target)
	if e != nil {
		return groupOperation{}, mapped(e)
	}
	if state != "pending" || target != version {
		return groupOperation{}, ErrConflict
	}
	op := groupOperation{ID: g.PendingOperationID, Group: g}
	if raw != nil {
		op.Baseline = &audienceBaseline{}
		if json.Unmarshal(raw, op.Baseline) != nil {
			return groupOperation{}, ErrUnavailable
		}
	}
	return op, nil
}
func pendingMatches(g Group, op groupOperation) bool {
	return g.Version == op.Group.Version && g.SyncState == "pending" && !g.Enabled && g.PendingOperationID == op.ID
}
func (s *Store) freezeGroupBaseline(ctx context.Context, op groupOperation, baseline audienceBaseline) (groupOperation, error) {
	e := s.groupTransaction(ctx, op.Group.ChannelID, op.Group.ChatID, func(tx pgx.Tx, g Group, _ []string) error {
		if !pendingMatches(g, op) {
			return ErrConflict
		}
		_, e := tx.Exec(ctx, "UPDATE channel_group_operations SET remote_baseline=$2 WHERE id=$1 AND remote_baseline IS NULL AND state='pending'", op.ID, encode(baseline))
		return e
	})
	if e != nil {
		return op, e
	}
	return s.pendingGroup(ctx, op.Group.ChannelID, op.Group.ChatID, op.Group.Version)
}
func (s *Store) groupSyncError(ctx context.Context, op groupOperation, code string) {
	// Safe codes only, never persist upstream bodies or the acting user's bearer.
	if code != "audience_unavailable" && code != "audience_denied" && code != "audience_conflict" {
		code = "audience_unavailable"
	}
	_ = s.groupTransaction(ctx, op.Group.ChannelID, op.Group.ChatID, func(tx pgx.Tx, g Group, _ []string) error {
		if !pendingMatches(g, op) {
			return ErrConflict
		}
		_, e := tx.Exec(ctx, "UPDATE channel_groups SET sync_error=$2 WHERE id=$1", g.ID, code)
		return e
	})
}
func (s *Store) finalizeGroup(ctx context.Context, actor string, op groupOperation, snapshot *AudienceSnapshot) (Group, error) {
	var out Group
	e := s.groupTransaction(ctx, op.Group.ChannelID, op.Group.ChatID, func(tx pgx.Tx, g Group, spaces []string) error {
		if !pendingMatches(g, op) {
			return ErrConflict
		}
		if g.DesiredEnabled && len(intersect(g.SpaceIDs, spaces)) != len(g.SpaceIDs) {
			return ErrConflict
		}
		version := int64(0)
		if snapshot == nil {
			if g.DesiredEnabled || op.Baseline == nil || op.Baseline.Exists {
				return ErrConflict
			}
		} else {
			if !snapshot.valid() || !sameAudienceState(*snapshot, desiredAudience(g)) {
				return ErrConflict
			}
			version = snapshot.Version
		}
		var e error
		out, e = scanGroup(tx.QueryRow(ctx, "UPDATE channel_groups SET enabled=desired_enabled,audience_version=$2,sync_state='synced',sync_error='',pending_operation_id='' WHERE id=$1 RETURNING "+groupCols, g.ID, version))
		if e != nil {
			return e
		}
		if _, e = tx.Exec(ctx, "UPDATE channel_group_operations SET state='completed' WHERE id=$1 AND state='pending'", op.ID); e != nil {
			return e
		}
		_, e = tx.Exec(ctx, "INSERT INTO channel_audit(actor,action,target) VALUES($1,'group.reconciled',$2)", actor, g.ID)
		return e
	})
	return out, e
}
