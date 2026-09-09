package auth

import (
	"errors"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"net/http"
	"strings"
)

type Handler struct {
	Verifier   *Verifier
	Client     *platform.Client
	IAMURL     string
	Delegation *DelegationBroker
}

func NewHandler(v *Verifier, c *platform.Client, iamURL string, sec *platform.ServiceSecurity, options ...func(*Handler)) http.Handler {
	h := &Handler{Verifier: v, Client: c, IAMURL: iamURL}
	for _, option := range options {
		option(h)
	}
	internal := http.NewServeMux()
	internal.HandleFunc("POST /internal/v1/resolve", h.resolve)
	internal.HandleFunc("POST /internal/v1/authorize", h.authorize)
	internal.HandleFunc("POST /internal/v1/authorize-batch", h.batch)
	internal.HandleFunc("POST /internal/v1/channel-token", h.channelToken)
	internal.HandleFunc("POST /internal/v1/channel-run-token", h.channelRunToken)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) { platform.JSON(w, 200, map[string]string{"status": "ok"}) })
	mux.Handle("/", sec.Middleware("auth", internal))
	return mux
}
func (h *Handler) identity(w http.ResponseWriter, r *http.Request) (platform.Resolved, bool) {
	if strings.HasPrefix(r.Header.Get("Authorization"), "Bearer "+channelReadPrefix) {
		return h.channelReadIdentity(w, r)
	}
	if strings.HasPrefix(r.Header.Get("Authorization"), "Bearer "+delegationPrefix) {
		return h.delegatedIdentity(w, r)
	}
	token, err := h.Verifier.Verify(r.Context(), r.Header.Get("Authorization"))
	if err != nil {
		platform.Error(w, r, 401, "unauthorized", "Valid user token required")
		return platform.Resolved{}, false
	}
	var p platform.Principal
	err = h.Client.Call(r.Context(), "iam", h.IAMURL, "POST", "/internal/v1/principals/ensure", "", map[string]string{"id": token.Subject, "name": token.Name, "email": token.Email, "azp": token.AuthorizedParty}, &p)
	if err != nil {
		var upstream *platform.HTTPError
		if errors.As(err, &upstream) && upstream.Status == 403 {
			platform.Error(w, r, 403, "forbidden", "Account is unavailable")
		} else {
			platform.Error(w, r, 503, "authorization_unavailable", "Authorization unavailable")
		}
		return platform.Resolved{}, false
	}
	return platform.Resolved{
		Principal: p, Scopes: strings.Fields(token.Scope),
		Audiences: append([]string{}, token.Audience...), Issuer: token.Issuer,
		ClientID: token.AuthorizedParty, ExpiresAt: token.ExpiresAt.Unix(),
	}, true
}
func (h *Handler) resolve(w http.ResponseWriter, r *http.Request) {
	var in struct{}
	if err := platform.Decode(w, r, &in); err != nil {
		platform.Error(w, r, 400, "invalid_request", "Expected empty JSON object")
		return
	}
	v, ok := h.identity(w, r)
	if ok {
		platform.JSON(w, 200, v)
	}
}
func scoped(v platform.Resolved, action string) bool {
	if v.Delegated && action != "read" {
		return false
	}
	scope := "knowledge:read"
	if action != "read" {
		scope = "knowledge:write"
	}
	for _, s := range v.Scopes {
		if s == scope {
			return true
		}
	}
	return false
}
func validRequest(in platform.AuthorizationRequest) bool {
	return (in.Action == "read" || in.Action == "write" || in.Action == "grant" || in.Action == "review") && in.SpaceID != "" && len(in.SpaceID) <= 256 && len(in.ResourceID) <= 256
}
func (h *Handler) decision(r *http.Request, p platform.Principal, in platform.AuthorizationRequest) (platform.Decision, error) {
	var out platform.Decision
	path := "/internal/v1/check"
	body := map[string]string{"principal_id": p.ID, "action": in.Action, "space_id": in.SpaceID, "resource_id": in.ResourceID}
	if c := p.ChannelContext; c != nil {
		if in.Action != "read" || !contains(c.SpaceIDs, in.SpaceID) {
			return platform.Decision{AuthEpoch: p.AuthEpoch}, nil
		}
		if c.ChatType == "group" {
			path = "/internal/v1/check-channel"
			body["audience_id"], body["channel_id"], body["group_key"] = c.AudienceID, c.ChannelID, c.GroupKey
		}
	}
	err := h.Client.Call(r.Context(), "iam", h.IAMURL, "POST", path, "", body, &out)
	return out, err
}
func (h *Handler) authorize(w http.ResponseWriter, r *http.Request) {
	var in platform.AuthorizationRequest
	if err := platform.Decode(w, r, &in); err != nil || !validRequest(in) {
		platform.Error(w, r, 400, "invalid_request", "Invalid authorization request")
		return
	}
	resolved, ok := h.identity(w, r)
	if !ok {
		return
	}
	if !scoped(resolved, in.Action) {
		platform.JSON(w, 200, platform.Decision{Allowed: false, AuthEpoch: resolved.Principal.AuthEpoch})
		return
	}
	d, err := h.decision(r, resolved.Principal, in)
	if err != nil {
		platform.Error(w, r, 503, "authorization_unavailable", "Authorization unavailable")
		return
	}
	platform.JSON(w, 200, d)
}
func (h *Handler) batch(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Action    string              `json:"action"`
		Resources []platform.Resource `json:"resources"`
	}
	if err := platform.Decode(w, r, &in); err != nil || len(in.Resources) > 1000 {
		platform.Error(w, r, 400, "invalid_request", "Invalid authorization batch")
		return
	}
	for _, v := range in.Resources {
		if !validRequest(platform.AuthorizationRequest{Action: in.Action, SpaceID: v.SpaceID, ResourceID: v.ResourceID}) {
			platform.Error(w, r, 400, "invalid_request", "Invalid authorization batch")
			return
		}
	}
	resolved, ok := h.identity(w, r)
	if !ok {
		return
	}
	type item struct {
		platform.Resource
		Allowed    bool   `json:"allowed"`
		ACLDomain  string `json:"acl_domain"`
		ACLVersion int64  `json:"acl_version"`
	}
	items := []item{}
	epoch := resolved.Principal.AuthEpoch
	for _, v := range in.Resources {
		d := platform.Decision{AuthEpoch: epoch}
		if scoped(resolved, in.Action) {
			var err error
			d, err = h.decision(r, resolved.Principal, platform.AuthorizationRequest{Action: in.Action, SpaceID: v.SpaceID, ResourceID: v.ResourceID})
			if err != nil || d.AuthEpoch != epoch {
				platform.Error(w, r, 503, "authorization_changed", "Authorization changed; retry batch")
				return
			}
		}
		items = append(items, item{Resource: v, Allowed: d.Allowed, ACLDomain: d.ACLDomain, ACLVersion: d.ACLVersion})
	}
	platform.JSON(w, 200, map[string]any{"decisions": items, "auth_epoch": epoch})
}
