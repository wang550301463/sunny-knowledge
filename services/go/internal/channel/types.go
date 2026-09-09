// Package channel owns WeCom credentials, transport, bindings and message coordination.
package channel

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"time"
	"unicode/utf8"
)

var (
	ErrInvalid         = errors.New("invalid channel request")
	ErrDenied          = errors.New("channel operation denied")
	ErrConflict        = errors.New("channel version conflict")
	ErrNotFound        = errors.New("channel record not found")
	ErrUnavailable     = errors.New("channel dependency unavailable")
	ErrLeaseLost       = errors.New("channel connection lease lost")
	ErrDeliveryUnknown = errors.New("channel delivery outcome unknown")
	ErrRejected        = errors.New("channel reply rejected")
	ErrDisplaced       = errors.New("channel connection displaced")
)

const DefaultWebSocketURL = "wss://openws.work.weixin.qq.com"
const MaxReplyBytes = 20480

// UnsentError proves the WebSocket write was never attempted. It is distinct
// from an uncertain network outcome, which must never be retried blindly.
type UnsentError struct{ cause error }

func (e *UnsentError) Error() string { return "channel frame was not sent" }
func (e *UnsentError) Unwrap() error { return e.cause }

type Config struct {
	ID               string   `json:"id"`
	Name             string   `json:"name"`
	BotID            string   `json:"bot_id"`
	AgentID          string   `json:"agent_id"`
	SpaceIDs         []string `json:"space_ids"`
	Version          int64    `json:"version"`
	Enabled          bool     `json:"enabled"`
	Status           string   `json:"status"`
	TestedVersion    int64    `json:"tested_version"`
	SecretConfigured bool     `json:"secret_configured"`
	SecretCipher     []byte   `json:"-"`

	AgentConfigurationID string `json:"agent_configuration_id"`
}
type ConfigInput struct {
	BaseVersion int64    `json:"base_version"`
	Name        string   `json:"name"`
	BotID       string   `json:"bot_id"`
	BotSecret   string   `json:"bot_secret"`
	AgentID     string   `json:"agent_id"`
	SpaceIDs    []string `json:"space_ids"`
	Enabled     bool     `json:"enabled"`

	AgentConfigurationID string `json:"agent_configuration_id"`
}

func (v ConfigInput) validate() error {
	if len(v.Name) < 1 || len(v.Name) > 160 || !key(v.BotID) || !key(v.AgentID) || len(v.BotSecret) > 4096 || len(v.SpaceIDs) < 1 || len(v.SpaceIDs) > 100 {
		return ErrInvalid
	}
	seen := map[string]bool{}
	for _, s := range v.SpaceIDs {
		if !key(s) || seen[s] {
			return ErrInvalid
		}
		seen[s] = true
	}
	return nil
}
func key(s string) bool {
	if len(s) == 0 || len(s) > 256 {
		return false
	}
	for _, r := range s {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || strings.ContainsRune("_.:-", r)) {
			return false
		}
	}
	return true
}

type Group struct {
	AudienceVersion    int64    `json:"audience_version"`
	DesiredEnabled     bool     `json:"desired_enabled"`
	SyncState          string   `json:"sync_state"`
	SyncError          string   `json:"sync_error"`
	PendingOperationID string   `json:"-"`
	ID                 string   `json:"id"`
	ChannelID          string   `json:"channel_id"`
	ChatID             string   `json:"chat_id"`
	AudienceID         string   `json:"audience_id"`
	SpaceIDs           []string `json:"space_ids"`
	Version            int64    `json:"version"`
	Enabled            bool     `json:"enabled"`
}
type GroupInput struct {
	RegistrationID            string   `json:"-"`
	AcknowledgedPublicToGroup bool     `json:"acknowledged_public_to_group"`
	BaseVersion               int64    `json:"base_version"`
	AudienceID                string   `json:"audience_id"`
	SpaceIDs                  []string `json:"space_ids"`
	Enabled                   bool     `json:"enabled"`
}
type RunContext struct {
	ID                   string    `json:"id"`
	UserID               string    `json:"user_id"`
	ExternalUserID       string    `json:"external_user_id"`
	ChannelID            string    `json:"channel_id"`
	ChannelVersion       int64     `json:"channel_version"`
	BindingVersion       int64     `json:"binding_version"`
	ConversationKey      string    `json:"conversation_key"`
	Generation           int64     `json:"generation"`
	Capabilities         []string  `json:"capabilities"`
	MessageID            string    `json:"message_id"`
	AgentID              string    `json:"agent_id"`
	AgentConfigurationID string    `json:"agent_configuration_id"`
	SpaceIDs             []string  `json:"space_ids"`
	ChatType             string    `json:"chat_type"`
	ChatID               string    `json:"chat_id"`
	AudienceID           string    `json:"audience_id"`
	GroupKey             string    `json:"group_key"`
	GroupVersion         int64     `json:"group_version"`
	ExpiresAt            time.Time `json:"expires_at"`
}
type Update struct {
	Content    string
	Finished   bool
	RunID      string
	URL        string
	BeforeSend func(context.Context) error
}

// AgentClient must exchange only the opaque context ID through the auth broker. It may
// not construct a delegated identity from UserID or use a privileged static bearer.
// Updates must already have passed current source ACL checks before this callback.
type AgentClient interface {
	Execute(context.Context, RunContext, string, string, func(Update) error) (string, error)
	Cancel(context.Context, RunContext) error
	Clear(context.Context, RunContext) error
}
type UnavailableAgent struct{}

func (UnavailableAgent) Execute(context.Context, RunContext, string, string, func(Update) error) (string, error) {
	return "", ErrUnavailable
}
func (UnavailableAgent) Cancel(context.Context, RunContext) error { return ErrUnavailable }
func (UnavailableAgent) Clear(context.Context, RunContext) error  { return ErrUnavailable }

// ConfigurationVerifier checks the delegated caller, selected published Agent and
// scope, and validates IAM's registered group audience. A nil verifier denies writes.
type ConfigurationVerifier interface {
	Agent(context.Context, string, string, []string) (string, error)
	Audience(context.Context, string, string, string, string, []string) error
	ReadAudience(context.Context, string, string) (AudienceSnapshot, error)
	CreateAudience(context.Context, string, AudienceSnapshot) error
	UpdateAudience(context.Context, string, AudienceSnapshot, int64) error
}

type SecretBox struct{ aead cipher.AEAD }

func NewSecretBox(key []byte) (*SecretBox, error) {
	if len(key) != 32 {
		return nil, ErrInvalid
	}
	b, e := aes.NewCipher(key)
	if e != nil {
		return nil, ErrInvalid
	}
	a, e := cipher.NewGCM(b)
	return &SecretBox{a}, e
}
func (b *SecretBox) Seal(id, secret string) ([]byte, error) {
	n := make([]byte, b.aead.NonceSize())
	if _, e := rand.Read(n); e != nil {
		return nil, ErrUnavailable
	}
	return b.aead.Seal(n, n, []byte(secret), []byte("channel:v1:"+id)), nil
}
func (b *SecretBox) Open(id string, sealed []byte) (string, error) {
	n := b.aead.NonceSize()
	if len(sealed) < n {
		return "", ErrUnavailable
	}
	p, e := b.aead.Open(nil, sealed[:n], sealed[n:], []byte("channel:v1:"+id))
	if e != nil {
		return "", ErrUnavailable
	}
	return string(p), nil
}
func digest(v any) string {
	b, _ := json.Marshal(v)
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}
func ConversationKey(c, u, kind, chat string, binding, config, group, generation int64) string {
	return digest([]any{c, u, kind, chat, binding, config, group, generation})
}
func BoundedReply(content, link string) string {
	if len(content) <= MaxReplyBytes {
		return content
	}
	suffix := "\n\n回答较长，以下为节选。"
	if link != "" {
		suffix += "[查看完整结果（需登录）](" + link + ")"
	}
	if len(suffix) > 2048 {
		suffix = "\n\n回答较长，请在知识平台查看完整结果。"
	}
	n := MaxReplyBytes - len(suffix)
	for n > 0 && !utf8.RuneStart(content[n]) {
		n--
	}
	return content[:n] + suffix
}
func validWebURL(s string) bool {
	u, e := url.Parse(s)
	return e == nil && (u.Scheme == "https" || u.Scheme == "http") && u.Host != "" && u.User == nil && u.RawQuery == "" && u.Fragment == ""
}

// MessageCommand strips a group @-mention so command matching sees only the verb.
func MessageCommand(m Message) string {
	text := strings.TrimSpace(m.Text)
	if m.ChatType == "group" && strings.HasPrefix(text, "@") {
		if i := strings.IndexFunc(text, func(r rune) bool { return r == ' ' || r == '\t' || r == '\n' || r == '\u2005' || r == '\u00a0' }); i >= 0 {
			return strings.TrimSpace(text[i:])
		}
	}
	return text
}
