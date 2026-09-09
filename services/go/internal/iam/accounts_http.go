package iam

import (
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"net/http"
)

func (h *Handler) createUser(w http.ResponseWriter, r *http.Request) {
	var in struct {
		ID    string `json:"id"`
		Name  string `json:"name"`
		Email string `json:"email"`
	}
	if !decode(w, r, &in) {
		return
	}
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	account, err := h.Store.CreateAccount(r.Context(), p.ID, AccountInput{ID: in.ID, Name: in.Name, Email: in.Email, Kind: "user"})
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 201, account)
}
func (h *Handler) createServiceAccount(w http.ResponseWriter, r *http.Request) {
	var in struct {
		ID       string `json:"id"`
		Name     string `json:"name"`
		ClientID string `json:"client_id"`
	}
	if !decode(w, r, &in) {
		return
	}
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	account, err := h.Store.CreateAccount(r.Context(), p.ID, AccountInput{ID: in.ID, Name: in.Name, Kind: "service", ClientID: in.ClientID})
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 201, account)
}
func (h *Handler) listServiceAccounts(w http.ResponseWriter, r *http.Request) {
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	accounts, err := h.Store.Accounts(r.Context(), p.ID, "service")
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, map[string]any{"items": accounts})
}
func (h *Handler) getServiceAccount(w http.ResponseWriter, r *http.Request) {
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	account, err := h.Store.Account(r.Context(), p.ID, r.PathValue("id"), "service")
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, account)
}
func (h *Handler) updateServiceAccount(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Name        string `json:"name"`
		Active      *bool  `json:"active"`
		BaseVersion int64  `json:"base_version"`
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
	account, err := h.Store.UpdateServiceAccount(r.Context(), p.ID, r.PathValue("id"), in.Name, *in.Active, in.BaseVersion)
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, account)
}
func (h *Handler) deleteServiceAccount(w http.ResponseWriter, r *http.Request) {
	var in struct {
		BaseVersion int64 `json:"base_version"`
	}
	if !decode(w, r, &in) {
		return
	}
	p, ok := h.resolve(w, r)
	if !ok {
		return
	}
	if err := h.Store.DeleteServiceAccount(r.Context(), p.ID, r.PathValue("id"), in.BaseVersion); err != nil {
		storeError(w, r, err)
		return
	}
	w.WriteHeader(204)
}
