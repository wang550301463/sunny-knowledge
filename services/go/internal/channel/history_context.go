package channel

import (
	"context"
	"encoding/json"
	"net/http"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

// This immutable context can only authorize viewing its recorded run. It is not
// a new incoming message, and cannot revive execution after expiry or lease loss.
type HistoricalRunContext struct {
	RunID   string     `json:"run_id"`
	Context RunContext `json:"context"`
}

func (s *Store) HistoricalContext(ctx context.Context, run string) (HistoricalRunContext, error) {
	if !key(run) {
		return HistoricalRunContext{}, ErrInvalid
	}
	var raw []byte
	var count int64
	err := s.db(ctx).QueryRow(ctx, `SELECT count(*),coalesce(jsonb_agg(x.payload),'[]'::jsonb)
 FROM channel_messages m JOIN channel_contexts x ON x.channel_id=m.channel_id AND x.message_id=m.message_id
 JOIN channel_configs c ON c.id=m.channel_id
 JOIN channel_bindings b ON b.channel_id=c.id AND b.external_user_id=m.external_user_id
 JOIN channel_sessions s ON s.channel_id=c.id AND s.external_user_id=m.external_user_id AND s.chat_type=m.chat_type AND s.chat_id=m.chat_id
 LEFT JOIN channel_groups g ON g.channel_id=c.id AND g.chat_id=m.chat_id
 WHERE m.run_id=$1 AND c.enabled AND b.active
 AND (SELECT count(*) FROM channel_messages WHERE run_id=$1)=1
 AND (SELECT count(*) FROM channel_contexts WHERE channel_id=m.channel_id AND message_id=m.message_id)=1
 AND x.payload->>'id'=x.id AND x.payload->>'channel_id'=m.channel_id AND x.payload->>'message_id'=m.message_id
 AND x.payload->>'external_user_id'=m.external_user_id AND x.payload->>'chat_type'=m.chat_type AND x.payload->>'chat_id'=m.chat_id
 AND c.version=(x.payload->>'channel_version')::bigint AND b.version=(x.payload->>'binding_version')::bigint AND b.user_id=x.payload->>'user_id'
 AND c.agent_id=x.payload->>'agent_id' AND c.agent_configuration_id=x.payload->>'agent_configuration_id'
 AND s.generation=(x.payload->>'generation')::bigint
 AND (m.chat_type='single' OR (m.chat_type='group' AND g.enabled AND g.id=x.payload->>'group_key' AND g.version=(x.payload->>'group_version')::bigint AND g.audience_id=x.payload->>'audience_id'))`, run).Scan(&count, &raw)
	if err != nil {
		return HistoricalRunContext{}, err
	}
	if count != 1 {
		return HistoricalRunContext{}, ErrDenied
	}
	var values []RunContext
	if json.Unmarshal(raw, &values) != nil || len(values) != 1 {
		return HistoricalRunContext{}, ErrUnavailable
	}
	return HistoricalRunContext{RunID: run, Context: values[0]}, nil
}

func (h *Handler) runContext(w http.ResponseWriter, r *http.Request) {
	if platform.RequireCaller(r, "auth") != nil {
		channelError(w, r, ErrDenied)
		return
	}
	out, err := h.Store.HistoricalContext(r.Context(), r.PathValue("run"))
	if err != nil {
		channelError(w, r, err)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	platform.JSON(w, 200, out)
}
