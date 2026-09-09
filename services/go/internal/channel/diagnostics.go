package channel

import (
	"context"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"net/http"
	"time"
)

type BindingView struct {
	ChannelID      string `json:"channel_id"`
	ExternalUserID string `json:"external_user_id"`
	Version        int64  `json:"version"`
	Active         bool   `json:"active"`
}

func (s *Store) Bindings(ctx context.Context, actor string) ([]BindingView, error) {
	rows, e := s.Pool.Query(ctx, "SELECT channel_id,external_user_id,version,active FROM channel_bindings WHERE user_id=$1 ORDER BY channel_id,external_user_id LIMIT 100", actor)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []BindingView{}
	for rows.Next() {
		var v BindingView
		if e = rows.Scan(&v.ChannelID, &v.ExternalUserID, &v.Version, &v.Active); e != nil {
			return nil, e
		}
		out = append(out, v)
	}
	return out, rows.Err()
}

type MessageStatus struct {
	MessageID string    `json:"message_id"`
	State     string    `json:"state"`
	RunID     string    `json:"run_id"`
	CreatedAt time.Time `json:"created_at"`
}

func (s *Store) Diagnostics(ctx context.Context, id string) ([]MessageStatus, error) {
	rows, e := s.Pool.Query(ctx, "SELECT message_id,state,run_id,created_at FROM channel_messages WHERE channel_id=$1 ORDER BY created_at DESC,message_id LIMIT 100", id)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []MessageStatus{}
	for rows.Next() {
		var v MessageStatus
		if e = rows.Scan(&v.MessageID, &v.State, &v.RunID, &v.CreatedAt); e != nil {
			return nil, e
		}
		out = append(out, v)
	}
	return out, rows.Err()
}
func (s *Store) RecordRun(ctx context.Context, l Lease, id, run string) error {
	if !key(run) {
		return ErrInvalid
	}
	_, e := s.Pool.Exec(ctx, "UPDATE channel_messages SET run_id=$5 WHERE channel_id=$1 AND message_id=$2 AND owner=$3 AND fence=$4", l.ChannelID, id, l.Owner, l.Fence, run)
	return e
}
func (h *Handler) bindings(w http.ResponseWriter, r *http.Request) {
	actor, ok := h.actor(w, r, false)
	if !ok {
		return
	}
	v, e := h.Store.Bindings(r.Context(), actor)
	if e != nil {
		channelError(w, r, e)
		return
	}
	platform.JSON(w, 200, map[string]any{"items": v})
}
func (h *Handler) diagnostics(w http.ResponseWriter, r *http.Request) {
	if _, ok := h.actor(w, r, true); !ok {
		return
	}
	v, e := h.Store.Diagnostics(r.Context(), r.PathValue("id"))
	if e != nil {
		channelError(w, r, e)
		return
	}
	platform.JSON(w, 200, map[string]any{"items": v})
}
