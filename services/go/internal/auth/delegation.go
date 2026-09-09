package auth

import (
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

const delegationPrefix = "skc_"
const delegationIssuer = "knowledge-channel-delegation"
const delegationAudience = "knowledge-internal-channel"

type DelegationBroker struct {
	private    ed25519.PrivateKey
	channelURL string
}

func NewDelegationBroker(private ed25519.PrivateKey, channelURL string) (*DelegationBroker, error) {
	u, err := url.Parse(channelURL)
	if len(private) != ed25519.PrivateKeySize || err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return nil, errors.New("invalid channel delegation configuration")
	}
	return &DelegationBroker{append(ed25519.PrivateKey{}, private...), strings.TrimRight(channelURL, "/")}, nil
}

func LoadDelegationBroker(path, channelURL string) (*DelegationBroker, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, errors.New("channel delegation key unavailable")
	}
	block, _ := pem.Decode(data)
	if block == nil {
		return nil, errors.New("invalid channel delegation key")
	}
	parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, errors.New("invalid channel delegation key")
	}
	key, ok := parsed.(ed25519.PrivateKey)
	if !ok {
		return nil, errors.New("Ed25519 channel delegation key required")
	}
	return NewDelegationBroker(key, channelURL)
}

func WithDelegation(broker *DelegationBroker) func(*Handler) {
	return func(h *Handler) { h.Delegation = broker }
}

// This is the channel service's versioned introspection wire contract, not its
// database model. The signature binds every isolation/fencing field returned.
type delegatedContext struct {
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

func contextKey(v string) bool {
	if len(v) == 0 || len(v) > 256 {
		return false
	}
	for _, c := range v {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.ContainsRune("_.:-", c)) {
			return false
		}
	}
	return true
}

func contains(values []string, value string) bool {
	for _, v := range values {
		if v == value {
			return true
		}
	}
	return false
}

func (v delegatedContext) valid(now time.Time) bool {
	for _, id := range []string{v.ID, v.UserID, v.ExternalUserID, v.ChannelID, v.ConversationKey, v.MessageID, v.AgentID, v.AgentConfigurationID} {
		if !contextKey(id) {
			return false
		}
	}
	if v.ChannelVersion < 1 || v.BindingVersion < 1 || v.Generation < 1 || !v.ExpiresAt.After(now) || v.ExpiresAt.After(now.Add(180*time.Second)) || len(v.Capabilities) != 1 || v.Capabilities[0] != "knowledge:read" || len(v.SpaceIDs) < 1 || len(v.SpaceIDs) > 100 {
		return false
	}
	seen := map[string]bool{}
	for _, space := range v.SpaceIDs {
		if !contextKey(space) || seen[space] {
			return false
		}
		seen[space] = true
	}
	if v.ChatType == "group" {
		return contextKey(v.ChatID) && contextKey(v.AudienceID) && contextKey(v.GroupKey) && v.GroupVersion > 0
	}
	return v.ChatType == "single" && v.AudienceID == "" && v.GroupKey == "" && v.GroupVersion == 0
}

func (v delegatedContext) hash() string {
	b, _ := json.Marshal(v)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func (v delegatedContext) constraint() *platform.ChannelConstraint {
	return &platform.ChannelConstraint{ContextID: v.ID, ChannelID: v.ChannelID, ConversationKey: v.ConversationKey, AgentID: v.AgentID, AgentConfigurationID: v.AgentConfigurationID, SpaceIDs: append([]string{}, v.SpaceIDs...), ChatType: v.ChatType, AudienceID: v.AudienceID, GroupKey: v.GroupKey, MessageID: v.MessageID}
}

type delegationClaims struct {
	jwt.RegisteredClaims
	ContextID   string `json:"context_id"`
	ContextHash string `json:"context_hash"`
}

func (h *Handler) channelContext(ctx context.Context, id string) (delegatedContext, error) {
	var v delegatedContext
	err := h.Client.Call(ctx, "channel", h.Delegation.channelURL, "GET", "/internal/v1/contexts/"+url.PathEscape(id), "", nil, &v)
	if err != nil {
		return v, err
	}
	if v.ID != id || !v.valid(time.Now()) {
		return v, &platform.HTTPError{Status: 403}
	}
	return v, nil
}

func (h *Handler) channelPrincipal(ctx context.Context, c delegatedContext) (platform.Principal, error) {
	var p platform.Principal
	err := h.Client.Call(ctx, "iam", h.IAMURL, "GET", "/internal/v1/principals/"+url.PathEscape(c.UserID), "", nil, &p)
	if err != nil {
		return p, err
	}
	if p.ID != c.UserID || !contains(p.Subjects, "user:"+p.ID) || p.AuthEpoch < 1 {
		return p, &platform.HTTPError{Status: 403}
	}
	for _, subject := range p.Subjects {
		if strings.HasPrefix(subject, "audience:") {
			return p, &platform.HTTPError{Status: 403}
		}
	}
	if c.ChatType == "group" {
		var audience struct {
			Allowed *bool `json:"allowed"`
			Version int64 `json:"version"`
			Epoch   int64 `json:"auth_epoch"`
		}
		err = h.Client.Call(ctx, "iam", h.IAMURL, "POST", "/internal/v1/channel-audiences/verify", "", map[string]any{"audience_id": c.AudienceID, "channel_id": c.ChannelID, "group_key": c.GroupKey, "space_ids": c.SpaceIDs}, &audience)
		if err != nil {
			return p, err
		}
		if audience.Allowed == nil || audience.Epoch != p.AuthEpoch {
			return p, &platform.HTTPError{Status: 503}
		}
		if !*audience.Allowed || audience.Version < 1 {
			return p, &platform.HTTPError{Status: 403}
		}
	}
	p.Permissions = []string{}
	p.ChannelContext = c.constraint()
	return p, nil
}

func delegationError(w http.ResponseWriter, r *http.Request, err error) {
	var upstream *platform.HTTPError
	if errors.As(err, &upstream) && (upstream.Status == 401 || upstream.Status == 403 || upstream.Status == 404) {
		platform.Error(w, r, 403, "channel_context_denied", "Channel context is no longer authorized")
		return
	}
	platform.Error(w, r, 503, "authorization_unavailable", "Channel authorization unavailable")
}

func (h *Handler) channelToken(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if platform.Caller(r.Context()) != "channel" {
		platform.Error(w, r, 403, "forbidden", "Channel workload required")
		return
	}
	var in struct {
		ContextID string `json:"context_id"`
	}
	if platform.Decode(w, r, &in) != nil || !contextKey(in.ContextID) {
		platform.Error(w, r, 400, "invalid_request", "An opaque channel context is required")
		return
	}
	if h.Delegation == nil {
		delegationError(w, r, errors.New("disabled"))
		return
	}
	c, err := h.channelContext(r.Context(), in.ContextID)
	if err != nil {
		delegationError(w, r, err)
		return
	}
	if _, err = h.channelPrincipal(r.Context(), c); err != nil {
		delegationError(w, r, err)
		return
	}
	now := time.Now()
	if !c.ExpiresAt.After(now.Add(time.Second)) {
		delegationError(w, r, &platform.HTTPError{Status: 403})
		return
	}
	claims := delegationClaims{RegisteredClaims: jwt.RegisteredClaims{Issuer: delegationIssuer, Subject: c.UserID, Audience: []string{delegationAudience}, ID: platform.ID(), IssuedAt: jwt.NewNumericDate(now), ExpiresAt: jwt.NewNumericDate(c.ExpiresAt)}, ContextID: c.ID, ContextHash: c.hash()}
	token, err := jwt.NewWithClaims(jwt.SigningMethodEdDSA, claims).SignedString(h.Delegation.private)
	if err != nil {
		delegationError(w, r, err)
		return
	}
	platform.JSON(w, 200, map[string]any{"access_token": delegationPrefix + token, "token_type": "Bearer", "expires_in": claims.ExpiresAt.Unix() - now.Unix(), "scope": "knowledge:read"})
}

func (h *Handler) delegatedIdentity(w http.ResponseWriter, r *http.Request) (platform.Resolved, bool) {
	w.Header().Set("Cache-Control", "no-store")
	if !contains([]string{"channel", "agent", "knowledge", "retrieval", "graphiti", "llm", "iam"}, platform.Caller(r.Context())) {
		platform.Error(w, r, 403, "forbidden", "Channel delegation is restricted to internal consumers")
		return platform.Resolved{}, false
	}
	if h.Delegation == nil {
		delegationError(w, r, errors.New("disabled"))
		return platform.Resolved{}, false
	}
	claims := new(delegationClaims)
	raw := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "+delegationPrefix)
	_, err := jwt.ParseWithClaims(raw, claims, func(t *jwt.Token) (any, error) { return h.Delegation.private.Public(), nil }, jwt.WithValidMethods([]string{"EdDSA"}), jwt.WithIssuer(delegationIssuer), jwt.WithAudience(delegationAudience), jwt.WithExpirationRequired(), jwt.WithIssuedAt())
	if err != nil || claims.IssuedAt == nil || claims.ExpiresAt == nil || !claims.ExpiresAt.After(claims.IssuedAt.Time) || claims.ExpiresAt.Sub(claims.IssuedAt.Time) > 180*time.Second || !contextKey(claims.ContextID) || len(claims.ContextHash) != 64 || len(claims.Audience) != 1 {
		platform.Error(w, r, 401, "unauthorized", "Valid channel delegation required")
		return platform.Resolved{}, false
	}
	c, err := h.channelContext(r.Context(), claims.ContextID)
	if err != nil {
		delegationError(w, r, err)
		return platform.Resolved{}, false
	}
	if c.hash() != claims.ContextHash || c.UserID != claims.Subject || claims.ExpiresAt.Unix() > c.ExpiresAt.Unix() {
		delegationError(w, r, &platform.HTTPError{Status: 403})
		return platform.Resolved{}, false
	}
	p, err := h.channelPrincipal(r.Context(), c)
	if err != nil {
		delegationError(w, r, err)
		return platform.Resolved{}, false
	}
	return platform.Resolved{Principal: p, Scopes: []string{"knowledge:read"}, Issuer: delegationIssuer, Audiences: []string{delegationAudience}, ClientID: "knowledge-channel", ExpiresAt: claims.ExpiresAt.Unix(), Delegated: true}, true
}

// validIdentity reports whether the delegated context still names a bound identity.
func (c delegatedContext) validIdentity() bool {
	return c.UserID != "" && c.ExternalUserID != "" && c.ConversationKey != ""
}
