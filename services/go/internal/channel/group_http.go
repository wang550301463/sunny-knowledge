package channel

import (
	"context"
	"errors"
	"net/http"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

func (h *Handler) group(w http.ResponseWriter, r *http.Request) {
	actor, ok := h.actor(w, r, true)
	if !ok {
		return
	}
	var in GroupInput
	if !body(w, r, &in) {
		return
	}
	if h.Verifier == nil {
		channelError(w, r, ErrUnavailable)
		return
	}
	g, e := h.Store.PrepareGroup(r.Context(), actor, r.PathValue("id"), r.PathValue("chat"), in)
	if e == nil {
		g, e = h.synchronizeGroup(r.Context(), actor, r.Header.Get("Authorization"), g.ChannelID, g.ChatID, g.Version)
	}
	if e != nil {
		channelError(w, r, e)
		return
	}
	platform.JSON(w, 200, g)
}
func (h *Handler) reconcileGroup(w http.ResponseWriter, r *http.Request) {
	actor, ok := h.actor(w, r, true)
	if !ok {
		return
	}
	var in struct {
		BaseVersion int64 `json:"base_version"`
	}
	if !body(w, r, &in) {
		return
	}
	if in.BaseVersion < 1 {
		channelError(w, r, ErrInvalid)
		return
	}
	if h.Verifier == nil {
		channelError(w, r, ErrUnavailable)
		return
	}
	g, e := h.Store.GetGroup(r.Context(), r.PathValue("id"), r.PathValue("chat"))
	if e == nil && g.Version != in.BaseVersion {
		e = ErrConflict
	}
	// Legacy enabled rows were blocked by migration. An explicit fresh request
	// creates an auditable intent; no background job retains user credentials.
	if e == nil && g.SyncState == "pending" && g.PendingOperationID == "" {
		g, e = h.Store.PrepareGroup(r.Context(), actor, g.ChannelID, g.ChatID, GroupInput{BaseVersion: g.Version, AudienceID: g.AudienceID, SpaceIDs: g.SpaceIDs, Enabled: g.DesiredEnabled, AcknowledgedPublicToGroup: g.DesiredEnabled})
	}
	if e == nil && g.SyncState == "synced" {
		channelError(w, r, ErrConflict)
		return
	}
	if e == nil {
		g, e = h.synchronizeGroup(r.Context(), actor, r.Header.Get("Authorization"), g.ChannelID, g.ChatID, g.Version)
	}
	if e != nil {
		channelError(w, r, e)
		return
	}
	platform.JSON(w, 200, g)
}
func (h *Handler) synchronizeGroup(ctx context.Context, actor, bearer, channel, chat string, version int64) (out Group, err error) {
	op, err := h.Store.pendingGroup(ctx, channel, chat, version)
	if err != nil {
		return out, err
	}
	defer func() {
		if err != nil {
			code := "audience_unavailable"
			if errors.Is(err, ErrDenied) {
				code = "audience_denied"
			}
			if errors.Is(err, ErrConflict) {
				code = "audience_conflict"
			}
			h.Store.groupSyncError(ctx, op, code)
		}
	}()
	current, e := h.Verifier.ReadAudience(ctx, bearer, op.Group.AudienceID)
	if e != nil && !errors.Is(e, ErrNotFound) {
		return out, e
	}
	exists := e == nil
	desired := desiredAudience(op.Group)
	if exists && (!current.valid() || current.ID != desired.ID || current.ChannelID != desired.ChannelID || current.GroupKey != desired.GroupKey) {
		return out, ErrConflict
	}
	if op.Baseline == nil {
		op, err = h.Store.freezeGroupBaseline(ctx, op, audienceBaseline{Exists: exists, Snapshot: current})
		if err != nil {
			return out, err
		}
	}
	baseline := op.Baseline
	if exists && sameAudienceState(current, desired) {
		if baseline.Exists && current.Version < baseline.Snapshot.Version {
			return out, ErrConflict
		}
		return h.Store.finalizeGroup(ctx, actor, op, &current)
	}
	if !exists && !baseline.Exists && !desired.Active {
		return h.Store.finalizeGroup(ctx, actor, op, nil)
	}
	if exists != baseline.Exists || exists && (!sameAudienceState(current, baseline.Snapshot) || current.Version != baseline.Snapshot.Version) {
		return out, ErrConflict
	}
	// CAS only the frozen baseline. Conflicts never silently adopt a newer version.
	// An explicit new PUT can supersede this intent after the user reviews it.
	if !exists {
		err = h.Verifier.CreateAudience(ctx, bearer, desired)
	} else {
		err = h.Verifier.UpdateAudience(ctx, bearer, desired, baseline.Snapshot.Version)
	}
	if err != nil {
		return out, err
	}
	current, err = h.Verifier.ReadAudience(ctx, bearer, desired.ID)
	if err != nil {
		return out, err
	}
	if !current.valid() || !sameAudienceState(current, desired) || (baseline.Exists && current.Version < baseline.Snapshot.Version) {
		return out, ErrConflict
	}
	return h.Store.finalizeGroup(ctx, actor, op, &current)
}
