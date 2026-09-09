package iam

import (
	"errors"
	"net/http"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

func (h *Handler) createAudience(w http.ResponseWriter, r *http.Request) {
	if !caller(w, r, "channel") {
		return
	}
	var in struct {
		ID           string   `json:"id"`
		ChannelID    string   `json:"channel_id"`
		GroupKey     string   `json:"group_key"`
		SpaceIDs     []string `json:"space_ids"`
		Acknowledged bool     `json:"acknowledged_public_to_group"`
	}
	if !decode(w, r, &in) {
		return
	}
	if !in.Acknowledged {
		platform.Error(w, r, 400, "group_disclosure_required", "Explicit group audience disclosure acknowledgement required")
		return
	}
	actor, ok := h.resolve(w, r)
	if !ok {
		return
	}
	out, err := h.Store.CreateAudience(r.Context(), actor.ID, ChannelAudience{ID: in.ID, ChannelID: in.ChannelID, GroupKey: in.GroupKey, SpaceIDs: in.SpaceIDs})
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 201, out)
}

func (h *Handler) updateAudience(w http.ResponseWriter, r *http.Request) {
	if !caller(w, r, "channel") {
		return
	}
	var in struct {
		ChannelID    string   `json:"channel_id"`
		GroupKey     string   `json:"group_key"`
		SpaceIDs     []string `json:"space_ids"`
		BaseVersion  int64    `json:"base_version"`
		Active       *bool    `json:"active"`
		Acknowledged bool     `json:"acknowledged_public_to_group"`
	}
	if !decode(w, r, &in) {
		return
	}
	if in.Active == nil || (*in.Active && !in.Acknowledged) {
		platform.Error(w, r, 400, "invalid_request", "An explicit state and group disclosure acknowledgement are required")
		return
	}
	actor, ok := h.resolve(w, r)
	if !ok {
		return
	}
	out, err := h.Store.UpdateAudience(r.Context(), actor.ID, r.PathValue("id"), in.ChannelID, in.GroupKey, in.BaseVersion, in.SpaceIDs, *in.Active)
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, out)
}

func (h *Handler) verifyAudience(w http.ResponseWriter, r *http.Request) {
	if !caller(w, r, "channel", "auth") {
		return
	}
	var in struct {
		ID        string   `json:"audience_id"`
		ChannelID string   `json:"channel_id"`
		GroupKey  string   `json:"group_key"`
		SpaceIDs  []string `json:"space_ids"`
	}
	if !decode(w, r, &in) {
		return
	}
	out, err := h.Store.VerifyAudience(r.Context(), in.ID, in.ChannelID, in.GroupKey, in.SpaceIDs)
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, out)
}

func (h *Handler) checkChannel(w http.ResponseWriter, r *http.Request) {
	if !caller(w, r, "auth") {
		return
	}
	var in struct {
		PrincipalID string `json:"principal_id"`
		AudienceID  string `json:"audience_id"`
		ChannelID   string `json:"channel_id"`
		GroupKey    string `json:"group_key"`
		Action      string `json:"action"`
		SpaceID     string `json:"space_id"`
		ResourceID  string `json:"resource_id"`
	}
	if !decode(w, r, &in) {
		return
	}
	out, err := h.Store.CheckChannel(r.Context(), in.PrincipalID, in.AudienceID, in.ChannelID, in.GroupKey, in.Action, in.SpaceID, in.ResourceID)
	if err != nil && !errors.Is(err, ErrDenied) {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, out)
}
