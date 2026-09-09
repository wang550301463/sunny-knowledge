package channel

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"github.com/jackc/pgx/v5"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"time"
)

type Challenge struct {
	ID        string    `json:"id"`
	WebToken  string    `json:"web_token"`
	ExpiresAt time.Time `json:"expires_at"`
}

func randomToken() string {
	b := make([]byte, 32)
	if _, e := rand.Read(b); e != nil {
		panic("secure randomness unavailable")
	}
	return base64.RawURLEncoding.EncodeToString(b)
}
func (s *Store) BeginChallenge(ctx context.Context, l Lease, external string) (Challenge, error) {
	if !key(external) {
		return Challenge{}, ErrInvalid
	}
	v := Challenge{ID: platform.ID(), WebToken: randomToken()}
	e := s.WithFence(ctx, l, func() error {
		tx, e := s.Pool.Begin(ctx)
		if e != nil {
			return e
		}
		defer tx.Rollback(ctx)
		if _, e = tx.Exec(ctx, "UPDATE channel_challenges SET state='revoked' WHERE channel_id=$1 AND external_user_id=$2 AND state IN ('pending','claimed')", l.ChannelID, external); e != nil {
			return e
		}
		e = tx.QueryRow(ctx, "INSERT INTO channel_challenges(id,channel_id,external_user_id,web_hash,expires_at) VALUES($1,$2,$3,$4,clock_timestamp()+interval '5 minutes') RETURNING expires_at", v.ID, l.ChannelID, external, digest(v.WebToken)).Scan(&v.ExpiresAt)
		if e != nil {
			return e
		}
		return tx.Commit(ctx)
	})
	return v, e
}

// actor is produced by live platform auth, never by a request JSON field.
func (s *Store) ClaimChallenge(ctx context.Context, actor, id, webToken string) (string, error) {
	if actor == "" || len(webToken) > 256 {
		return "", ErrDenied
	}
	proof := randomToken()
	r, e := s.Pool.Exec(ctx, "UPDATE channel_challenges SET state='claimed',user_id=$2,confirmation_hash=$3 WHERE id=$1 AND web_hash=$4 AND state='pending' AND expires_at>clock_timestamp()", id, actor, digest(proof), digest(webToken))
	if e != nil {
		return "", e
	}
	if r.RowsAffected() != 1 {
		return "", ErrDenied
	}
	return proof, nil
}
func (s *Store) ConfirmChallenge(ctx context.Context, l Lease, external, id, proof string) error {
	return s.WithFence(ctx, l, func() error {
		tx, e := s.Pool.Begin(ctx)
		if e != nil {
			return e
		}
		defer tx.Rollback(ctx)
		var user string
		e = tx.QueryRow(ctx, "UPDATE channel_challenges SET state='consumed' WHERE id=$1 AND channel_id=$2 AND external_user_id=$3 AND confirmation_hash=$4 AND state='claimed' AND expires_at>clock_timestamp() RETURNING user_id", id, l.ChannelID, external, digest(proof)).Scan(&user)
		if errors.Is(e, pgx.ErrNoRows) {
			return ErrDenied
		}
		if e != nil {
			return e
		}
		if _, e = tx.Exec(ctx, "UPDATE channel_bindings SET active=false,version=version+1 WHERE channel_id=$1 AND user_id=$2 AND external_user_id<>$3 AND active", l.ChannelID, user, external); e != nil {
			return e
		}
		if _, e = tx.Exec(ctx, "INSERT INTO channel_bindings(channel_id,external_user_id,user_id) VALUES($1,$2,$3) ON CONFLICT(channel_id,external_user_id) DO UPDATE SET user_id=$3,active=true,version=channel_bindings.version+1", l.ChannelID, external, user); e != nil {
			return mapped(e)
		}
		if _, e = tx.Exec(ctx, "INSERT INTO channel_audit(actor,action,target) VALUES($1,'identity.bind',$2)", user, l.ChannelID+":"+external); e != nil {
			return e
		}
		return tx.Commit(ctx)
	})
}
func (s *Store) Unbind(ctx context.Context, actor, channel, external string) error {
	tx, e := s.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer tx.Rollback(ctx)
	r, e := tx.Exec(ctx, "UPDATE channel_bindings SET active=false,version=version+1 WHERE channel_id=$1 AND external_user_id=$2 AND user_id=$3 AND active", channel, external, actor)
	if e != nil {
		return e
	}
	if r.RowsAffected() != 1 {
		return ErrDenied
	}
	if _, e = tx.Exec(ctx, "INSERT INTO channel_audit(actor,action,target) VALUES($1,'identity.unbind',$2)", actor, channel+":"+external); e != nil {
		return e
	}
	return tx.Commit(ctx)
}
func (s *Store) CreateContext(ctx context.Context, l Lease, m Message) (RunContext, error) {
	var v RunContext
	e := s.WithFence(ctx, l, func() error {
		tx, e := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead})
		if e != nil {
			return e
		}
		defer tx.Rollback(ctx)
		v = RunContext{ID: platform.ID(), ChannelID: l.ChannelID, ExternalUserID: m.UserID, MessageID: m.ID, ChatType: m.ChatType, ChatID: m.ChatID, Capabilities: []string{"knowledge:read"}}
		if e = tx.QueryRow(ctx, "SELECT c.version,c.agent_id,c.space_ids,b.user_id,b.version FROM channel_configs c JOIN channel_bindings b ON b.channel_id=c.id WHERE c.id=$1 AND c.enabled AND b.external_user_id=$2 AND b.active", l.ChannelID, m.UserID).Scan(&v.ChannelVersion, &v.AgentID, &v.SpaceIDs, &v.UserID, &v.BindingVersion); errors.Is(e, pgx.ErrNoRows) {
			return ErrDenied
		} else if e != nil {
			return e
		}
		var matches bool
		if e = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM channel_messages WHERE channel_id=$1 AND message_id=$2 AND external_user_id=$3 AND chat_type=$4 AND chat_id=$5 AND owner=$6 AND fence=$7 AND state='received')", l.ChannelID, m.ID, m.UserID, m.ChatType, m.ChatID, l.Owner, l.Fence).Scan(&matches); e != nil {
			return e
		}
		if !matches {
			return ErrDenied
		}
		if m.ChatType == "group" {
			if e = tx.QueryRow(ctx, "SELECT id,audience_id,space_ids,version FROM channel_groups WHERE channel_id=$1 AND chat_id=$2 AND enabled", l.ChannelID, m.ChatID).Scan(&v.GroupKey, &v.AudienceID, &v.SpaceIDs, &v.GroupVersion); errors.Is(e, pgx.ErrNoRows) {
				return ErrDenied
			} else if e != nil {
				return e
			}
			var configured []string
			if e = tx.QueryRow(ctx, "SELECT space_ids FROM channel_configs WHERE id=$1", l.ChannelID).Scan(&configured); e != nil {
				return e
			}
			v.SpaceIDs = intersect(v.SpaceIDs, configured)
			if len(v.SpaceIDs) == 0 {
				return ErrDenied
			}
		}
		e = tx.QueryRow(ctx, "INSERT INTO channel_sessions(channel_id,external_user_id,chat_type,chat_id) VALUES($1,$2,$3,$4) ON CONFLICT(channel_id,external_user_id,chat_type,chat_id) DO UPDATE SET generation=channel_sessions.generation RETURNING generation", l.ChannelID, m.UserID, m.ChatType, m.ChatID).Scan(&v.Generation)
		if e != nil {
			return e
		}
		v.ConversationKey = ConversationKey(l.ChannelID, m.UserID, m.ChatType, m.ChatID, v.BindingVersion, v.ChannelVersion, v.GroupVersion, v.Generation)
		if e = tx.QueryRow(ctx, "SELECT clock_timestamp()+interval '180 seconds'").Scan(&v.ExpiresAt); e != nil {
			return e
		}
		_, e = tx.Exec(ctx, "INSERT INTO channel_contexts(id,channel_id,message_id,owner,fence,payload,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7)", v.ID, l.ChannelID, m.ID, l.Owner, l.Fence, encode(v), v.ExpiresAt)
		if e != nil {
			return e
		}
		return tx.Commit(ctx)
	})
	return v, e
}
func intersect(a, b []string) []string {
	out := []string{}
	for _, x := range a {
		for _, y := range b {
			if x == y {
				out = append(out, x)
				break
			}
		}
	}
	return out
}
func (s *Store) Introspect(ctx context.Context, id string) (RunContext, error) {
	var v RunContext
	var raw []byte
	e := s.Pool.QueryRow(ctx, `SELECT x.payload FROM channel_contexts x JOIN channel_configs c ON c.id=x.channel_id
 JOIN channel_bindings b ON b.channel_id=c.id AND b.external_user_id=x.payload->>'external_user_id'
 JOIN channel_sessions s ON s.channel_id=c.id AND s.external_user_id=b.external_user_id AND s.chat_type=x.payload->>'chat_type' AND s.chat_id=x.payload->>'chat_id'
 JOIN channel_leases l ON l.channel_id=c.id AND l.owner=x.owner AND l.fence=x.fence
 LEFT JOIN channel_groups g ON g.channel_id=c.id AND g.chat_id=x.payload->>'chat_id'
 WHERE x.id=$1 AND x.expires_at>clock_timestamp() AND l.until_at>clock_timestamp() AND c.enabled AND b.active
 AND c.version=(x.payload->>'channel_version')::bigint AND b.version=(x.payload->>'binding_version')::bigint AND b.user_id=x.payload->>'user_id'
 AND s.generation=(x.payload->>'generation')::bigint
 AND (x.payload->>'chat_type'='single' OR (g.enabled AND g.version=(x.payload->>'group_version')::bigint AND g.audience_id=x.payload->>'audience_id'))`, id).Scan(&raw)
	if errors.Is(e, pgx.ErrNoRows) {
		return v, ErrDenied
	}
	if e != nil {
		return v, e
	}
	if json.Unmarshal(raw, &v) != nil {
		return RunContext{}, ErrUnavailable
	}
	return v, nil
}
func (s *Store) ClearConversation(ctx context.Context, v RunContext) error {
	if _, e := s.Introspect(ctx, v.ID); e != nil {
		return e
	}
	r, e := s.Pool.Exec(ctx, "UPDATE channel_sessions SET generation=generation+1 WHERE channel_id=$1 AND external_user_id=$2 AND chat_type=$3 AND chat_id=$4 AND generation=$5", v.ChannelID, v.ExternalUserID, v.ChatType, v.ChatID, v.Generation)
	if e != nil {
		return e
	}
	if r.RowsAffected() != 1 {
		return ErrConflict
	}
	return nil
}
