package iam

import (
	"errors"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"net/http"
	"net/url"
	"strings"
)

type Handler struct {
	Store   *Store
	Client  *platform.Client
	AuthURL string
}

func NewHandler(s *Store, c *platform.Client, authURL string, sec *platform.ServiceSecurity) http.Handler {
	h := &Handler{s, c, authURL}
	internal := http.NewServeMux()
	internal.HandleFunc("POST /internal/v1/principals/ensure", h.ensure)
	internal.HandleFunc("GET /internal/v1/principals/{sub}", h.principal)
	internal.HandleFunc("POST /internal/v1/check", h.check)
	internal.HandleFunc("GET /internal/v1/policies/{space}/{resource}", h.policy)
	internal.HandleFunc("GET /internal/v1/policies/{space}", h.policy)
	internal.HandleFunc("POST /internal/v1/resources", h.resource)
	public := http.NewServeMux()
	public.HandleFunc("GET /api/v1/me", h.me)
	public.HandleFunc("GET /api/v1/spaces", h.listSpaces)
	public.HandleFunc("POST /api/v1/spaces", h.createSpace)
	public.HandleFunc("GET /api/v1/spaces/{id}", h.getSpace)
	for _, kind := range []string{"groups", "departments"} {
		public.HandleFunc("GET /api/v1/"+kind, h.listGroups)
		public.HandleFunc("POST /api/v1/"+kind, h.createGroup)
		public.HandleFunc("GET /api/v1/"+kind+"/{id}/members", h.listMembers)
		public.HandleFunc("PUT /api/v1/"+kind+"/{id}/members/{user}", h.member)
		public.HandleFunc("DELETE /api/v1/"+kind+"/{id}/members/{user}", h.member)
	}
	public.HandleFunc("GET /api/v1/users", h.listUsers)
	public.HandleFunc("PUT /api/v1/users/{id}", h.updateUser)
	public.HandleFunc("GET /api/v1/grants", h.getGrants)
	public.HandleFunc("PUT /api/v1/grants", h.grant)
	public.HandleFunc("GET /api/v1/audit", h.audit)
	secured := http.NewServeMux()
	secured.Handle("/internal/", internal)
	secured.Handle("/api/", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !caller(w, r, "gateway") {
			return
		}
		public.ServeHTTP(w, r)
	}))
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) { platform.JSON(w, 200, map[string]string{"status": "ok"}) })
	mux.Handle("/", sec.Middleware("iam", secured))
	return mux
}
func caller(w http.ResponseWriter, r *http.Request, names ...string) bool {
	if platform.RequireCaller(r, names...) != nil {
		platform.Error(w, r, 403, "forbidden", "Operation denied")
		return false
	}
	return true
}
func storeError(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, ErrDenied):
		platform.Error(w, r, 403, "forbidden", "Operation denied")
	case errors.Is(err, ErrInvalid):
		platform.Error(w, r, 400, "invalid_request", "Invalid request")
	default:
		platform.Error(w, r, 503, "iam_unavailable", "IAM unavailable")
	}
}
func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	if err := platform.Decode(w, r, v); err != nil {
		platform.Error(w, r, 400, "invalid_request", "Invalid JSON body")
		return false
	}
	return true
}
func (h *Handler) resolve(w http.ResponseWriter, r *http.Request) (platform.Principal, bool) {
	v, err := h.Client.Resolve(r.Context(), h.AuthURL, r.Header.Get("Authorization"))
	if err != nil {
		var upstream *platform.HTTPError
		if errors.As(err, &upstream) && (upstream.Status == 401 || upstream.Status == 403) {
			platform.Error(w, r, upstream.Status, "unauthorized", "Valid active user required")
		} else {
			platform.Error(w, r, 503, "authorization_unavailable", "Authorization unavailable")
		}
		return platform.Principal{}, false
	}
	return v.Principal, true
}
func (h *Handler) ensure(w http.ResponseWriter, r *http.Request) {
	if !caller(w, r, "auth") {
		return
	}
	var in struct {
		ID    string `json:"id"`
		Name  string `json:"name"`
		Email string `json:"email"`
	}
	if !decode(w, r, &in) {
		return
	}
	p, err := h.Store.Ensure(r.Context(), in.ID, in.Name, in.Email)
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, p)
}
func (h *Handler) principal(w http.ResponseWriter, r *http.Request) {
	if !caller(w, r, "auth") {
		return
	}
	p, err := h.Store.Principal(r.Context(), r.PathValue("sub"))
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, p)
}
func (h *Handler) check(w http.ResponseWriter, r *http.Request) {
	if !caller(w, r, "auth") {
		return
	}
	var in struct {
		PrincipalID string `json:"principal_id"`
		Action      string `json:"action"`
		SpaceID     string `json:"space_id"`
		ResourceID  string `json:"resource_id"`
	}
	if !decode(w, r, &in) {
		return
	}
	d, err := h.Store.Check(r.Context(), in.PrincipalID, in.Action, in.SpaceID, in.ResourceID)
	if errors.Is(err, ErrDenied) {
		platform.JSON(w, 200, platform.Decision{Allowed: false})
		return
	}
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, d)
}
func (h *Handler) policy(w http.ResponseWriter, r *http.Request) {
	if !caller(w, r, "auth", "knowledge", "ingest", "retrieval", "graphiti") {
		return
	}
	p, err := h.Store.Policy(r.Context(), r.PathValue("space"), r.PathValue("resource"))
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, p)
}
func (h *Handler) resource(w http.ResponseWriter, r *http.Request) {
	if !caller(w, r, "knowledge") {
		return
	}
	var in platform.Resource
	if !decode(w, r, &in) {
		return
	}
	if err := h.Store.RegisterResource(r.Context(), in.SpaceID, in.ResourceID); err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 201, in)
}
func (h *Handler) me(w http.ResponseWriter, r *http.Request) {
	p, ok := h.resolve(w, r)
	if ok {
		platform.JSON(w, 200, p)
	}
}
func (h *Handler) createSpace(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Name string `json:"name"`
	}
	if !decode(w, r, &in) {
		return
	}
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	v, err := h.Store.CreateSpace(r.Context(), p.ID, in.Name)
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 201, v)
}
func (h *Handler) listSpaces(w http.ResponseWriter, r *http.Request) {
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	v, err := h.Store.Spaces(r.Context(), p.ID)
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, map[string]any{"items": v})
}
func (h *Handler) getSpace(w http.ResponseWriter, r *http.Request) {
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	v, err := h.Store.Spaces(r.Context(), p.ID)
	if err != nil {
		storeError(w, r, err)
		return
	}
	for _, s := range v {
		if s.ID == r.PathValue("id") {
			platform.JSON(w, 200, s)
			return
		}
	}
	storeError(w, r, ErrDenied)
}
func groupKind(path string) string {
	if strings.Contains(path, "/departments") {
		return "department"
	}
	return "group"
}
func (h *Handler) createGroup(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Name string `json:"name"`
	}
	if !decode(w, r, &in) {
		return
	}
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	v, err := h.Store.CreateGroup(r.Context(), p.ID, groupKind(r.URL.Path), in.Name)
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 201, v)
}
func (h *Handler) listGroups(w http.ResponseWriter, r *http.Request) {
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	v, err := h.Store.Groups(r.Context(), p.ID, groupKind(r.URL.Path))
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, map[string]any{"items": v})
}
func (h *Handler) member(w http.ResponseWriter, r *http.Request) {
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	if err := h.Store.SetMember(r.Context(), p.ID, r.PathValue("id"), r.PathValue("user"), r.Method == "PUT"); err != nil {
		storeError(w, r, err)
		return
	}
	w.WriteHeader(204)
}
func (h *Handler) listMembers(w http.ResponseWriter, r *http.Request) {
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	v, err := h.Store.Members(r.Context(), p.ID, r.PathValue("id"))
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, map[string]any{"items": v})
}
func (h *Handler) listUsers(w http.ResponseWriter, r *http.Request) {
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	v, err := h.Store.Users(r.Context(), p.ID)
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, map[string]any{"items": v})
}
func (h *Handler) updateUser(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Name   string `json:"name"`
		Active *bool  `json:"active"`
	}
	if !decode(w, r, &in) {
		return
	}
	if in.Active == nil {
		storeError(w, r, ErrInvalid)
		return
	}
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	if err := h.Store.SetUser(r.Context(), p.ID, r.PathValue("id"), in.Name, *in.Active); err != nil {
		storeError(w, r, err)
		return
	}
	w.WriteHeader(204)
}
func (h *Handler) grant(w http.ResponseWriter, r *http.Request) {
	var in struct {
		SpaceID    string       `json:"space_id"`
		ResourceID string       `json:"resource_id"`
		Action     string       `json:"action"`
		Subjects   jsonSubjects `json:"subjects"`
	}
	if !decode(w, r, &in) {
		return
	}
	if !in.Subjects.Present {
		storeError(w, r, ErrInvalid)
		return
	}
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	if err := h.Store.SetGrant(r.Context(), p.ID, in.SpaceID, in.ResourceID, in.Action, in.Subjects.Value); err != nil {
		storeError(w, r, err)
		return
	}
	pol, err := h.Store.Policy(r.Context(), in.SpaceID, in.ResourceID)
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, pol)
}
func (h *Handler) getGrants(w http.ResponseWriter, r *http.Request) {
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	space, resource := r.URL.Query().Get("space_id"), r.URL.Query().Get("resource_id")
	d, err := h.Store.Check(r.Context(), p.ID, "grant", space, resource)
	if err != nil || !d.Allowed {
		storeError(w, r, ErrDenied)
		return
	}
	v, err := h.Store.Grants(r.Context(), space, resource)
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, map[string]any{"items": v})
}
func (h *Handler) audit(w http.ResponseWriter, r *http.Request) {
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	v, err := h.Store.Audit(r.Context(), p.ID)
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, map[string]any{"items": v})
}

var _ = url.PathEscape
