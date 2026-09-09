package channel

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"time"
)

//go:embed schema.sql
var schema string

type Store struct {
	Pool *pgxpool.Pool
	Box  *SecretBox
}

func NewStore(p *pgxpool.Pool, b *SecretBox) *Store { return &Store{p, b} }
func (s *Store) Migrate(ctx context.Context) error  { _, e := s.Pool.Exec(ctx, schema); return e }
func mapped(e error) error {
	if errors.Is(e, pgx.ErrNoRows) {
		return ErrNotFound
	}
	var p *pgconn.PgError
	if errors.As(e, &p) && (p.Code == "23505" || p.Code == "40001") {
		return ErrConflict
	}
	return e
}

const configCols = "id,name,bot_id,agent_id,space_ids,version,enabled,status,tested_version,secret_cipher"

func scanConfig(r pgx.Row) (Config, error) {
	var c Config
	e := r.Scan(&c.ID, &c.Name, &c.BotID, &c.AgentID, &c.SpaceIDs, &c.Version, &c.Enabled, &c.Status, &c.TestedVersion, &c.SecretCipher)
	c.SecretConfigured = len(c.SecretCipher) > 0
	return c, mapped(e)
}
func (s *Store) GetConfig(ctx context.Context, id string) (Config, error) {
	return scanConfig(s.Pool.QueryRow(ctx, "SELECT "+configCols+" FROM channel_configs WHERE id=$1", id))
}
func (s *Store) ListConfigs(ctx context.Context) ([]Config, error) {
	r, e := s.Pool.Query(ctx, "SELECT "+configCols+" FROM channel_configs ORDER BY id LIMIT 100")
	if e != nil {
		return nil, e
	}
	defer r.Close()
	out := []Config{}
	for r.Next() {
		c, e := scanConfig(r)
		if e != nil {
			return nil, e
		}
		out = append(out, c)
	}
	return out, r.Err()
}
func (s *Store) SaveConfig(ctx context.Context, actor, id string, in ConfigInput) (Config, error) {
	if in.validate() != nil || actor == "" {
		return Config{}, ErrInvalid
	}
	tx, e := s.Pool.Begin(ctx)
	if e != nil {
		return Config{}, e
	}
	defer tx.Rollback(ctx)
	var c Config
	if id == "" {
		if in.BaseVersion != 0 || in.BotSecret == "" || in.Enabled {
			return c, ErrInvalid
		}
		id = platform.ID()
		cipher, e := s.Box.Seal(id, in.BotSecret)
		if e != nil {
			return c, e
		}
		c, e = scanConfig(tx.QueryRow(ctx, "INSERT INTO channel_configs(id,name,bot_id,agent_id,space_ids,secret_cipher) VALUES($1,$2,$3,$4,$5,$6) RETURNING "+configCols, id, in.Name, in.BotID, in.AgentID, in.SpaceIDs, cipher))
		if e != nil {
			return c, e
		}
	} else {
		c, e = scanConfig(tx.QueryRow(ctx, "SELECT "+configCols+" FROM channel_configs WHERE id=$1 FOR UPDATE", id))
		if e != nil {
			return c, e
		}
		if c.Version != in.BaseVersion {
			return Config{}, ErrConflict
		}
		if c.BotID != in.BotID {
			return Config{}, ErrInvalid
		}
		if in.Enabled && (c.TestedVersion != c.Version || in.BotSecret != "") {
			return Config{}, ErrConflict
		}
		cipher := c.SecretCipher
		if in.BotSecret != "" {
			cipher, e = s.Box.Seal(id, in.BotSecret)
			if e != nil {
				return Config{}, e
			}
		}
		tested := int64(0)
		if in.BotSecret == "" && c.TestedVersion == c.Version {
			tested = c.Version + 1
		}
		c, e = scanConfig(tx.QueryRow(ctx, "UPDATE channel_configs SET name=$2,agent_id=$3,space_ids=$4,secret_cipher=$5,enabled=$6,version=version+1,status=CASE WHEN $6 THEN 'connecting' ELSE 'disabled' END,tested_version=$7,updated_at=now() WHERE id=$1 RETURNING "+configCols, id, in.Name, in.AgentID, in.SpaceIDs, cipher, in.Enabled, tested))
		if e != nil {
			return c, e
		}
		_, e = tx.Exec(ctx, "UPDATE channel_leases SET until_at=now() WHERE channel_id=$1", id)
		if e != nil {
			return c, e
		}
	}
	if _, e = tx.Exec(ctx, "INSERT INTO channel_audit(actor,action,target) VALUES($1,'configuration.save',$2)", actor, id); e != nil {
		return c, e
	}
	return c, tx.Commit(ctx)
}
func (s *Store) RequestTest(ctx context.Context, id string, version int64) error {
	r, e := s.Pool.Exec(ctx, "UPDATE channel_configs SET test_requested=true,status='test_pending' WHERE id=$1 AND version=$2 AND enabled=false", id, version)
	if e != nil {
		return e
	}
	if r.RowsAffected() != 1 {
		return ErrConflict
	}
	return nil
}
func (s *Store) Runnable(ctx context.Context) ([]Config, error) {
	r, e := s.Pool.Query(ctx, "SELECT "+configCols+" FROM channel_configs WHERE enabled OR test_requested ORDER BY id LIMIT 100")
	if e != nil {
		return nil, e
	}
	defer r.Close()
	out := []Config{}
	for r.Next() {
		c, e := scanConfig(r)
		if e != nil {
			return nil, e
		}
		out = append(out, c)
	}
	return out, r.Err()
}
func (s *Store) SaveGroup(ctx context.Context, actor, channel, chat string, in GroupInput) (Group, error) {
	if !key(channel) || !key(chat) || !key(in.AudienceID) || len(in.SpaceIDs) == 0 || len(in.SpaceIDs) > 100 {
		return Group{}, ErrInvalid
	}
	tx, e := s.Pool.Begin(ctx)
	if e != nil {
		return Group{}, e
	}
	defer tx.Rollback(ctx)
	var spaces []string
	if e = tx.QueryRow(ctx, "SELECT space_ids FROM channel_configs WHERE id=$1 FOR UPDATE", channel).Scan(&spaces); e != nil {
		return Group{}, mapped(e)
	}
	for _, id := range in.SpaceIDs {
		found := false
		for _, s := range spaces {
			found = found || s == id
		}
		if !found {
			return Group{}, ErrInvalid
		}
	}
	var g Group
	query := "INSERT INTO channel_groups(id,channel_id,chat_id,audience_id,space_ids,enabled) VALUES($1,$2,$3,$4,$5,$6) RETURNING id,channel_id,chat_id,audience_id,space_ids,version,enabled"
	if in.BaseVersion == 0 {
		e = tx.QueryRow(ctx, query, platform.ID(), channel, chat, in.AudienceID, in.SpaceIDs, in.Enabled).Scan(&g.ID, &g.ChannelID, &g.ChatID, &g.AudienceID, &g.SpaceIDs, &g.Version, &g.Enabled)
	} else {
		e = tx.QueryRow(ctx, "UPDATE channel_groups SET audience_id=$3,space_ids=$4,enabled=$5,version=version+1 WHERE channel_id=$1 AND chat_id=$2 AND version=$6 RETURNING id,channel_id,chat_id,audience_id,space_ids,version,enabled", channel, chat, in.AudienceID, in.SpaceIDs, in.Enabled, in.BaseVersion).Scan(&g.ID, &g.ChannelID, &g.ChatID, &g.AudienceID, &g.SpaceIDs, &g.Version, &g.Enabled)
		if errors.Is(e, pgx.ErrNoRows) {
			e = ErrConflict
		}
	}
	if e != nil {
		return g, mapped(e)
	}
	if _, e = tx.Exec(ctx, "INSERT INTO channel_audit(actor,action,target) VALUES($1,'group.save',$2)", actor, g.ID); e != nil {
		return g, e
	}
	return g, tx.Commit(ctx)
}

type Lease struct {
	ChannelID string
	Owner     string
	Fence     int64
	Until     time.Time
}

func (s *Store) Acquire(ctx context.Context, id, owner string, ttl time.Duration) (Lease, error) {
	var l Lease
	l.ChannelID, l.Owner = id, owner
	e := s.Pool.QueryRow(ctx, "INSERT INTO channel_leases(channel_id,owner,fence,until_at) VALUES($1,$2,1,clock_timestamp()+$3*interval '1 millisecond') ON CONFLICT(channel_id) DO UPDATE SET owner=$2,fence=channel_leases.fence+1,until_at=clock_timestamp()+$3*interval '1 millisecond' WHERE channel_leases.until_at<=clock_timestamp() RETURNING fence,until_at", id, owner, ttl.Milliseconds()).Scan(&l.Fence, &l.Until)
	if errors.Is(e, pgx.ErrNoRows) {
		e = ErrLeaseLost
	}
	return l, e
}
func (s *Store) Renew(ctx context.Context, l Lease, ttl time.Duration) error {
	r, e := s.Pool.Exec(ctx, "UPDATE channel_leases SET until_at=clock_timestamp()+$4*interval '1 millisecond' WHERE channel_id=$1 AND owner=$2 AND fence=$3 AND until_at>clock_timestamp()", l.ChannelID, l.Owner, l.Fence, ttl.Milliseconds())
	if e != nil {
		return e
	}
	if r.RowsAffected() != 1 {
		return ErrLeaseLost
	}
	return nil
}
func (s *Store) CheckLease(ctx context.Context, l Lease) error {
	var ok bool
	e := s.Pool.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM channel_leases WHERE channel_id=$1 AND owner=$2 AND fence=$3 AND until_at>clock_timestamp())", l.ChannelID, l.Owner, l.Fence).Scan(&ok)
	if e != nil {
		return e
	}
	if !ok {
		return ErrLeaseLost
	}
	return nil
}
func (s *Store) Release(ctx context.Context, l Lease) error {
	_, e := s.Pool.Exec(ctx, "UPDATE channel_leases SET until_at=clock_timestamp() WHERE channel_id=$1 AND owner=$2 AND fence=$3", l.ChannelID, l.Owner, l.Fence)
	return e
}

// WithFence holds the row lock through the bounded network write. A replacement
// owner cannot acquire the lease until that write finishes, even at expiry.
func (s *Store) WithFence(ctx context.Context, l Lease, fn func() error) error {
	tx, e := s.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer tx.Rollback(ctx)
	var owner string
	var fence int64
	var valid bool
	e = tx.QueryRow(ctx, "SELECT owner,fence,until_at>clock_timestamp() FROM channel_leases WHERE channel_id=$1 FOR UPDATE", l.ChannelID).Scan(&owner, &fence, &valid)
	if e != nil {
		return e
	}
	if owner != l.Owner || fence != l.Fence || !valid {
		return ErrLeaseLost
	}
	if e = fn(); e != nil {
		return e
	}
	return tx.Commit(ctx)
}
func (s *Store) Accept(ctx context.Context, l Lease, m Message) (bool, error) {
	fresh := false
	e := s.WithFence(ctx, l, func() error {
		cipher, e := s.Box.Seal(l.ChannelID+":"+m.ID, m.Text)
		if e != nil {
			return e
		}
		r, e := s.Pool.Exec(ctx, "INSERT INTO channel_messages(channel_id,message_id,request_id,external_user_id,chat_type,chat_id,question_cipher,owner,fence) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING", l.ChannelID, m.ID, m.RequestID, m.UserID, m.ChatType, m.ChatID, cipher, l.Owner, l.Fence)
		fresh = e == nil && r.RowsAffected() == 1
		return e
	})
	return fresh, e
}
func (s *Store) Status(ctx context.Context, l Lease, version int64, status string, tested bool) error {
	return s.WithFence(ctx, l, func() error {
		r, e := s.Pool.Exec(ctx, "UPDATE channel_configs SET status=$3,tested_version=CASE WHEN $4 THEN version ELSE tested_version END,test_requested=CASE WHEN $4 OR $3 IN ('auth_failed','displaced','connection_failed') THEN false ELSE test_requested END WHERE id=$1 AND version=$2", l.ChannelID, version, status, tested)
		if e != nil {
			return e
		}
		if r.RowsAffected() != 1 {
			return ErrConflict
		}
		return nil
	})
}
func (s *Store) FinishMessage(ctx context.Context, l Lease, id, state, run string) error {
	_, e := s.Pool.Exec(ctx, "UPDATE channel_messages SET state=$5,run_id=$6 WHERE channel_id=$1 AND message_id=$2 AND owner=$3 AND fence=$4", l.ChannelID, id, l.Owner, l.Fence, state, run)
	return e
}
func (s *Store) BeginDelivery(ctx context.Context, l Lease, m string, seq int64, content string, finished bool) (string, error) {
	id := platform.ID()
	e := s.WithFence(ctx, l, func() error {
		_, e := s.Pool.Exec(ctx, "INSERT INTO channel_deliveries(id,channel_id,message_id,sequence,content_hash,finished) SELECT $1,$2,$3,$4,$5,$6 WHERE NOT EXISTS(SELECT 1 FROM channel_deliveries WHERE channel_id=$2 AND message_id=$3 AND state IN ('pending','delivery_unknown'))", id, l.ChannelID, m, seq, digest(content), finished)
		return e
	})
	return id, e
}
func (s *Store) EndDelivery(ctx context.Context, id, state string) error {
	_, e := s.Pool.Exec(ctx, "UPDATE channel_deliveries SET state=$2 WHERE id=$1 AND state='pending'", id, state)
	return e
}
func (s *Store) Recover(ctx context.Context, l Lease) error {
	return s.WithFence(ctx, l, func() error {
		_, e := s.Pool.Exec(ctx, "UPDATE channel_deliveries SET state='delivery_unknown' WHERE channel_id=$1 AND state='pending';", l.ChannelID)
		if e != nil {
			return e
		}
		_, e = s.Pool.Exec(ctx, "UPDATE channel_messages SET state='interrupted' WHERE channel_id=$1 AND fence<>$2 AND state='received'", l.ChannelID, l.Fence)
		return e
	})
}
func encode(v any) []byte { b, _ := json.Marshal(v); return b }
