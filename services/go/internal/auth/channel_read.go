package auth

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

const channelReadPrefix = "skr_"
const channelReadIssuer = "knowledge-channel-run-read"
const channelReadAudience = "knowledge-internal-channel-run-read"

type channelReadClaims struct {
	jwt.RegisteredClaims
	RunID       string `json:"run_id"`
	ContextID   string `json:"context_id"`
	ContextHash string `json:"context_hash"`
}

func (h *Handler) channelRunContext(ctx context.Context, id string) (delegatedContext, error) {
	var out struct {
		RunID   string           `json:"run_id"`
		Context delegatedContext `json:"context"`
	}
	err := h.Client.Call(ctx, "channel", h.Delegation.channelURL, "GET", "/internal/v1/runs/"+url.PathEscape(id)+"/context", "", nil, &out)
	if err != nil {
		return delegatedContext{}, err
	}
	// The original message expiry remains immutable and is hashed. Historical read
	// has its own short-lived credential; it never revives that message's lease.
	if out.RunID != id || !out.Context.validIdentity() || out.Context.ExpiresAt.IsZero() || out.Context.ExpiresAt.After(time.Now().Add(180*time.Second)) {
		return delegatedContext{}, &platform.HTTPError{Status: 403}
	}
	return out.Context, nil
}

func (h *Handler) channelRunToken(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if platform.Caller(r.Context()) != "agent" {
		platform.Error(w, r, 403, "forbidden", "Agent workload required")
		return
	}
	var in struct {
		RunID string `json:"run_id"`
	}
	if platform.Decode(w, r, &in) != nil || !contextKey(in.RunID) {
		platform.Error(w, r, 400, "invalid_request", "An opaque run ID is required")
		return
	}
	if h.Delegation == nil {
		delegationError(w, r, errors.New("disabled"))
		return
	}
	if strings.HasPrefix(r.Header.Get("Authorization"), "Bearer "+delegationPrefix) || strings.HasPrefix(r.Header.Get("Authorization"), "Bearer "+channelReadPrefix) {
		platform.Error(w, r, 403, "forbidden", "Direct user authorization required")
		return
	}
	direct, ok := h.identity(w, r)
	if !ok {
		return
	}
	if direct.Delegated || direct.Principal.ChannelContext != nil || !contains(direct.Scopes, "knowledge:read") || !contains(direct.Principal.Subjects, "user:"+direct.Principal.ID) {
		platform.Error(w, r, 403, "forbidden", "Direct user read authorization required")
		return
	}
	c, err := h.channelRunContext(r.Context(), in.RunID)
	if err != nil {
		delegationError(w, r, err)
		return
	}
	if c.UserID != direct.Principal.ID {
		delegationError(w, r, &platform.HTTPError{Status: 403})
		return
	}
	p, err := h.channelPrincipal(r.Context(), c)
	if err != nil {
		delegationError(w, r, err)
		return
	}
	if p.AuthEpoch != direct.Principal.AuthEpoch {
		delegationError(w, r, errors.New("authorization changed"))
		return
	}
	now := time.Now()
	expires := now.Add(180 * time.Second)
	if original := time.Unix(direct.ExpiresAt, 0); original.Before(expires) {
		expires = original
	}
	if !expires.After(now.Add(time.Second)) {
		delegationError(w, r, &platform.HTTPError{Status: 403})
		return
	}
	claims := channelReadClaims{RegisteredClaims: jwt.RegisteredClaims{Issuer: channelReadIssuer, Audience: []string{channelReadAudience}, Subject: c.UserID, ID: platform.ID(), IssuedAt: jwt.NewNumericDate(now), ExpiresAt: jwt.NewNumericDate(expires)}, RunID: in.RunID, ContextID: c.ID, ContextHash: c.hash()}
	token, err := jwt.NewWithClaims(jwt.SigningMethodEdDSA, claims).SignedString(h.Delegation.private)
	if err != nil {
		delegationError(w, r, err)
		return
	}
	platform.JSON(w, 200, map[string]any{"access_token": channelReadPrefix + token, "token_type": "Bearer", "expires_in": claims.ExpiresAt.Unix() - now.Unix(), "scope": "knowledge:read"})
}

func (h *Handler) channelReadIdentity(w http.ResponseWriter, r *http.Request) (platform.Resolved, bool) {
	w.Header().Set("Cache-Control", "no-store")
	if !contains([]string{"agent", "knowledge"}, platform.Caller(r.Context())) {
		platform.Error(w, r, 403, "forbidden", "Run read delegation is restricted to Agent and canonical evidence")
		return platform.Resolved{}, false
	}
	if h.Delegation == nil {
		delegationError(w, r, errors.New("disabled"))
		return platform.Resolved{}, false
	}
	claims := new(channelReadClaims)
	raw := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "+channelReadPrefix)
	_, err := jwt.ParseWithClaims(raw, claims, func(t *jwt.Token) (any, error) { return h.Delegation.private.Public(), nil }, jwt.WithValidMethods([]string{"EdDSA"}), jwt.WithIssuer(channelReadIssuer), jwt.WithAudience(channelReadAudience), jwt.WithExpirationRequired(), jwt.WithIssuedAt())
	if err != nil || claims.IssuedAt == nil || claims.ExpiresAt == nil || !claims.ExpiresAt.After(claims.IssuedAt.Time) || claims.ExpiresAt.Sub(claims.IssuedAt.Time) > 180*time.Second || len(claims.Audience) != 1 || !contextKey(claims.RunID) || !contextKey(claims.ContextID) || len(claims.ContextHash) != 64 {
		platform.Error(w, r, 401, "unauthorized", "Valid run read delegation required")
		return platform.Resolved{}, false
	}
	c, err := h.channelRunContext(r.Context(), claims.RunID)
	if err != nil {
		delegationError(w, r, err)
		return platform.Resolved{}, false
	}
	if c.ID != claims.ContextID || c.hash() != claims.ContextHash || c.UserID != claims.Subject {
		delegationError(w, r, &platform.HTTPError{Status: 403})
		return platform.Resolved{}, false
	}
	p, err := h.channelPrincipal(r.Context(), c)
	if err != nil {
		delegationError(w, r, err)
		return platform.Resolved{}, false
	}
	p.ChannelContext.ReadRunID = claims.RunID
	return platform.Resolved{Principal: p, Scopes: []string{"knowledge:read"}, Issuer: channelReadIssuer, Audiences: []string{channelReadAudience}, ClientID: "knowledge-channel-run-read", ExpiresAt: claims.ExpiresAt.Unix(), Delegated: true}, true
}
