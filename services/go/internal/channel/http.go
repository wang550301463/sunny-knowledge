package channel

import (
	"errors"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"io"
	"net/http"
	"slices"
)

type Handler struct {
	Store    *Store
	Client   *platform.Client
	AuthURL  string
	Verifier ConfigurationVerifier
}

func NewHandler(s *Store, c *platform.Client, auth string, sec *platform.ServiceSecurity, v ConfigurationVerifier) http.Handler {
	h := &Handler{s, c, auth, v}
	routes := http.NewServeMux()
	routes.HandleFunc("GET /internal/v1/contexts/{id}", func(w http.ResponseWriter, r *http.Request) {
		if platform.RequireCaller(r, "auth") != nil {
			channelError(w, r, ErrDenied)
			return
		}
		v, e := s.Introspect(r.Context(), r.PathValue("id"))
		if e != nil {
			channelError(w, r, e)
			return
		}
		platform.JSON(w, 200, v)
	})
	routes.HandleFunc("GET /api/v1/channels", h.list)
	routes.HandleFunc("POST /api/v1/channels", h.save)
	routes.HandleFunc("GET /api/v1/channels/{id}", h.get)
	routes.HandleFunc("PUT /api/v1/channels/{id}", h.save)
	routes.HandleFunc("POST /api/v1/channels/{id}/test", h.test)
	routes.HandleFunc("GET /api/v1/channels/{id}/groups", h.groups)
	routes.HandleFunc("PUT /api/v1/channels/{id}/groups/{chat}", h.group)
	routes.HandleFunc("POST /api/v1/channel-bindings/claim", h.claim)
	routes.HandleFunc("DELETE /api/v1/channels/{id}/bindings/{external}", h.unbind)
	secured := sec.Middleware("channel", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		if len(r.URL.Path) >= 5 && r.URL.Path[:5] == "/api/" && platform.RequireCaller(r, "gateway") != nil {
			channelError(w, r, ErrDenied)
			return
		}
		routes.ServeHTTP(w, r)
	}))
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		platform.JSON(w, 200, map[string]string{"status": "ok", "service": "channel"})
	})
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, r *http.Request) {
		if s.Pool.Ping(r.Context()) != nil {
			channelError(w, r, ErrUnavailable)
			return
		}
		platform.JSON(w, 200, map[string]string{"status": "ready"})
	})
	mux.Handle("/", secured)
	return mux
}
func channelError(w http.ResponseWriter, r *http.Request, e error) {
	status, code := 503, "channel_unavailable"
	switch {
	case errors.Is(e, ErrDenied):
		status, code = 403, "forbidden"
	case errors.Is(e, ErrInvalid):
		status, code = 400, "invalid_request"
	case errors.Is(e, ErrConflict):
		status, code = 409, "version_conflict"
	case errors.Is(e, ErrNotFound):
		status, code = 404, "not_found"
	}
	var upstream *platform.HTTPError
	if errors.As(e, &upstream) && (upstream.Status == 401 || upstream.Status == 403) {
		status, code = upstream.Status, "unauthorized"
	}
	platform.Error(w, r, status, code, "Channel request could not be completed")
}
func (h *Handler) actor(w http.ResponseWriter, r *http.Request, admin bool) (string, bool) {
	v, e := h.Client.Resolve(r.Context(), h.AuthURL, r.Header.Get("Authorization"))
	if e != nil {
		channelError(w, r, e)
		return "", false
	}
	scope := "knowledge:read"
	if r.Method != "GET" {
		scope = "knowledge:write"
	}
	if !slices.Contains(v.Scopes, scope) || v.Principal.ID == "" || (admin && !platform.HasPermission(v.Principal, "platform_admin")) {
		channelError(w, r, ErrDenied)
		return "", false
	}
	return v.Principal.ID, true
}
func body(w http.ResponseWriter, r *http.Request, v any) bool {
	raw, e := io.ReadAll(http.MaxBytesReader(w, r.Body, 1<<20))
	if e != nil || strictJSON(raw, v) != nil {
		channelError(w, r, ErrInvalid)
		return false
	}
	return true
}
func (h *Handler) list(w http.ResponseWriter, r *http.Request) {
	if _, ok := h.actor(w, r, true); !ok {
		return
	}
	v, e := h.Store.ListConfigs(r.Context())
	if e != nil {
		channelError(w, r, e)
		return
	}
	platform.JSON(w, 200, map[string]any{"items": v})
}
func (h *Handler) get(w http.ResponseWriter, r *http.Request) {
	if _, ok := h.actor(w, r, true); !ok {
		return
	}
	v, e := h.Store.GetConfig(r.Context(), r.PathValue("id"))
	if e != nil {
		channelError(w, r, e)
		return
	}
	platform.JSON(w, 200, v)
}
func (h *Handler) save(w http.ResponseWriter, r *http.Request) {
	actor, ok := h.actor(w, r, true)
	if !ok {
		return
	}
	var in ConfigInput
	if !body(w, r, &in) {
		return
	}
	if h.Verifier == nil {
		channelError(w, r, ErrUnavailable)
		return
	}
	configuration, e := h.Verifier.Agent(r.Context(), r.Header.Get("Authorization"), in.AgentID, in.SpaceIDs)
	if e != nil {
		channelError(w, r, e)
		return
	}
	in.AgentConfigurationID = configuration
	v, e := h.Store.SaveConfig(r.Context(), actor, r.PathValue("id"), in)
	if e != nil {
		channelError(w, r, e)
		return
	}
	status := 200
	if r.Method == "POST" {
		status = 201
	}
	platform.JSON(w, status, v)
}
func (h *Handler) test(w http.ResponseWriter, r *http.Request) {
	if _, ok := h.actor(w, r, true); !ok {
		return
	}
	var in struct {
		BaseVersion int64 `json:"base_version"`
	}
	if !body(w, r, &in) {
		return
	}
	if e := h.Store.RequestTest(r.Context(), r.PathValue("id"), in.BaseVersion); e != nil {
		channelError(w, r, e)
		return
	}
	platform.JSON(w, 202, map[string]string{"status": "test_pending"})
}
func (h *Handler) groups(w http.ResponseWriter, r *http.Request) {
	if _, ok := h.actor(w, r, true); !ok {
		return
	}
	v, e := h.Store.ListGroups(r.Context(), r.PathValue("id"))
	if e != nil {
		channelError(w, r, e)
		return
	}
	platform.JSON(w, 200, map[string]any{"items": v})
}
func (h *Handler) claim(w http.ResponseWriter, r *http.Request) {
	actor, ok := h.actor(w, r, false)
	if !ok {
		return
	}
	var in struct {
		ChallengeID string `json:"challenge_id"`
		WebToken    string `json:"web_token"`
	}
	if !body(w, r, &in) {
		return
	}
	proof, e := h.Store.ClaimChallenge(r.Context(), actor, in.ChallengeID, in.WebToken)
	if e != nil {
		channelError(w, r, e)
		return
	}
	platform.JSON(w, 200, map[string]string{"confirmation_command": "/确认 " + in.ChallengeID + " " + proof})
}
func (h *Handler) unbind(w http.ResponseWriter, r *http.Request) {
	actor, ok := h.actor(w, r, false)
	if !ok {
		return
	}
	if e := h.Store.Unbind(r.Context(), actor, r.PathValue("id"), r.PathValue("external")); e != nil {
		channelError(w, r, e)
		return
	}
	platform.JSON(w, 200, map[string]string{"status": "unbound"})
}
