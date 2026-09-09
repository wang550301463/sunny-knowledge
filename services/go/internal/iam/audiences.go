package iam

import (
	"context"
	"errors"
	"slices"

	"github.com/jackc/pgx/v5"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

type ChannelAudience struct {
	ID        string   `json:"id"`
	ChannelID string   `json:"channel_id"`
	GroupKey  string   `json:"group_key"`
	SpaceIDs  []string `json:"space_ids"`
	Active    bool     `json:"active"`
	Version   int64    `json:"version"`
}

func validAudience(in ChannelAudience) bool {
	if !validID(in.ID) || !validID(in.ChannelID) || !validID(in.GroupKey) || len(in.SpaceIDs) < 1 || len(in.SpaceIDs) > 100 || len(Canonical(in.SpaceIDs)) != len(in.SpaceIDs) {
		return false
	}
	for _, id := range in.SpaceIDs {
		if !validID(id) {
			return false
		}
	}
	return true
}

func audience(ctx context.Context, tx pgx.Tx, id string) (out ChannelAudience, err error) {
	err = tx.QueryRow(ctx, "SELECT id,channel_id,group_key,space_ids,active,version FROM iam_channel_audiences WHERE id=$1", id).Scan(&out.ID, &out.ChannelID, &out.GroupKey, &out.SpaceIDs, &out.Active, &out.Version)
	if errors.Is(err, pgx.ErrNoRows) {
		err = ErrNotFound
	}
	return
}

func audienceManager(ctx context.Context, tx pgx.Tx, actor string, spaces []string) error {
	for _, space := range spaces {
		for _, action := range []string{"read", "grant"} {
			d, err := check(ctx, tx, actor, action, space, "")
			if err != nil {
				return err
			}
			if !d.Allowed {
				return ErrDenied
			}
		}
	}
	return nil
}

func setAudienceSpaceRead(ctx context.Context, tx pgx.Tx, id string, spaces []string, enabled bool, version int64) error {
	for _, space := range spaces {
		acl, _, err := readSubjects(ctx, tx, space, "", "read")
		if err != nil {
			return err
		}
		subject := "audience:" + id
		if enabled {
			acl = Canonical(append(acl, subject))
			if len(acl) > 10000 {
				return ErrInvalid
			}
		} else {
			acl = slices.DeleteFunc(acl, func(s string) bool { return s == subject })
		}
		if _, err = tx.Exec(ctx, "UPDATE iam_policies SET subjects=$2,version=$3 WHERE space_id=$1 AND resource_id='' AND action='read'", space, acl, version); err != nil {
			return err
		}
	}
	return nil
}

// The caller has explicitly acknowledged disclosure to the group's present and future
// audience. Registration and the additive space READ grants commit in the same audit tx.
// Resource-level restrictions and every user's ordinary permissions remain unchanged.
func (s *Store) CreateAudience(ctx context.Context, actor string, in ChannelAudience) (out ChannelAudience, err error) {
	if !validAudience(in) {
		return out, ErrInvalid
	}
	in.SpaceIDs = Canonical(in.SpaceIDs)
	err = s.mutate(ctx, actor, "channel_audience.create", in.ID, in, func(tx pgx.Tx, n int64) error {
		if err := audienceManager(ctx, tx, actor, in.SpaceIDs); err != nil {
			return err
		}
		previous, err := audience(ctx, tx, in.ID)
		if err == nil {
			if previous.ChannelID != in.ChannelID || previous.GroupKey != in.GroupKey || !previous.Active || !slices.Equal(previous.SpaceIDs, in.SpaceIDs) {
				return ErrConflict
			}
			out = previous
			return errNoChange
		}
		if !errors.Is(err, ErrNotFound) {
			return err
		}
		var exists bool
		if err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM iam_channel_audiences WHERE channel_id=$1 AND group_key=$2)", in.ChannelID, in.GroupKey).Scan(&exists); err != nil {
			return err
		}
		if exists {
			return ErrConflict
		}
		out = in
		out.Active, out.Version = true, 1
		if _, err = tx.Exec(ctx, "INSERT INTO iam_channel_audiences(id,channel_id,group_key,space_ids,created_by) VALUES($1,$2,$3,$4,$5)", in.ID, in.ChannelID, in.GroupKey, in.SpaceIDs, actor); err != nil {
			return err
		}
		return setAudienceSpaceRead(ctx, tx, in.ID, in.SpaceIDs, true, n)
	})
	return
}

func (s *Store) UpdateAudience(ctx context.Context, actor, id, channelID, groupKey string, baseVersion int64, spaceIDs []string, active bool) (out ChannelAudience, err error) {
	if baseVersion < 1 || !validAudience(ChannelAudience{ID: id, ChannelID: channelID, GroupKey: groupKey, SpaceIDs: spaceIDs}) {
		return out, ErrInvalid
	}
	spaceIDs = Canonical(spaceIDs)
	err = s.mutate(ctx, actor, "channel_audience.update", id, map[string]any{"base_version": baseVersion, "space_ids": spaceIDs, "active": active}, func(tx pgx.Tx, n int64) error {
		previous, err := audience(ctx, tx, id)
		if err != nil {
			return err
		}
		if previous.ChannelID != channelID || previous.GroupKey != groupKey {
			return ErrConflict
		}
		if err = audienceManager(ctx, tx, actor, Canonical(append(append([]string{}, previous.SpaceIDs...), spaceIDs...))); err != nil {
			return err
		}
		if previous.Version != baseVersion {
			return ErrConflict
		}
		out = previous
		if previous.Active == active && slices.Equal(previous.SpaceIDs, spaceIDs) {
			return errNoChange
		}
		if err = setAudienceSpaceRead(ctx, tx, id, previous.SpaceIDs, false, n); err != nil {
			return err
		}
		if active {
			if err = setAudienceSpaceRead(ctx, tx, id, spaceIDs, true, n); err != nil {
				return err
			}
		}
		out.Active, out.SpaceIDs, out.Version = active, spaceIDs, previous.Version+1
		_, err = tx.Exec(ctx, "UPDATE iam_channel_audiences SET space_ids=$2,active=$3,version=$4 WHERE id=$1", id, spaceIDs, active, out.Version)
		return err
	})
	return
}

type AudienceVerification struct {
	Allowed   bool  `json:"allowed"`
	Version   int64 `json:"version"`
	AuthEpoch int64 `json:"auth_epoch"`
}

type AudienceSnapshot struct {
	ChannelAudience
	AuthEpoch int64 `json:"auth_epoch"`
}

// Read both active and inactive committed state for lost-response reconciliation.
// The complete registry and current read+grant authority come from one snapshot;
// this read never changes grants, audit history or the IAM epoch.
func (s *Store) AudienceSnapshot(ctx context.Context, actor, id string) (out AudienceSnapshot, err error) {
	if !validID(actor) || !validID(id) {
		return out, ErrInvalid
	}
	err = s.read(ctx, func(tx pgx.Tx) error {
		group, err := audience(ctx, tx, id)
		if err != nil {
			return err
		}
		if err = audienceManager(ctx, tx, actor, group.SpaceIDs); err != nil {
			return err
		}
		out.AuthEpoch, err = epoch(ctx, tx)
		if err != nil {
			return err
		}
		out.ChannelAudience = group
		return nil
	})
	return
}

func (s *Store) VerifyAudience(ctx context.Context, id, channelID, groupKey string, spaceIDs []string) (out AudienceVerification, err error) {
	if !validAudience(ChannelAudience{ID: id, ChannelID: channelID, GroupKey: groupKey, SpaceIDs: spaceIDs}) {
		return out, ErrInvalid
	}
	err = s.read(ctx, func(tx pgx.Tx) error {
		var err error
		out.AuthEpoch, err = epoch(ctx, tx)
		if err != nil {
			return err
		}
		group, err := audience(ctx, tx, id)
		if errors.Is(err, ErrNotFound) {
			return nil
		}
		if err != nil {
			return err
		}
		if !group.Active || group.ChannelID != channelID || group.GroupKey != groupKey {
			return nil
		}
		for _, space := range spaceIDs {
			if !slices.Contains(group.SpaceIDs, space) {
				return nil
			}
		}
		out.Allowed, out.Version = true, group.Version
		return nil
	})
	return
}

// One current IAM transaction checks the actor AND the audience, never their union.
func (s *Store) CheckChannel(ctx context.Context, actor, audienceID, channelID, groupKey, action, space, resource string) (out platform.Decision, err error) {
	if !validID(actor) || !validID(audienceID) || !validID(channelID) || !validID(groupKey) || !validID(space) || (resource != "" && !validID(resource)) || !validAction(action) {
		return out, ErrInvalid
	}
	err = s.read(ctx, func(tx pgx.Tx) error {
		var err error
		out, err = check(ctx, tx, actor, action, space, resource)
		if err != nil {
			return err
		}
		actorAllowed := out.Allowed
		out.Allowed = false
		if !actorAllowed || (action != "read" && action != "write") {
			return nil
		}
		group, err := audience(ctx, tx, audienceID)
		if errors.Is(err, ErrNotFound) {
			return nil
		}
		if err != nil {
			return err
		}
		if !group.Active || group.ChannelID != channelID || group.GroupKey != groupKey || !slices.Contains(group.SpaceIDs, space) {
			return nil
		}
		pol, err := policy(ctx, tx, space, resource)
		if err != nil {
			return err
		}
		out.Allowed = Allowed([]string{"audience:" + audienceID}, pol.SpaceReadSubjects, pol.ResourceReadSubjects)
		return nil
	})
	return
}
