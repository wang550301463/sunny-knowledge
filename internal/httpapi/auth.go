package httpapi

import (
	"net/http"
	"strings"
)

func roleOK(role string, allowed ...string) bool {
	for _, a := range allowed {
		if role == a {
			return true
		}
	}
	return false
}

func requireAuth(token, want string, allowed []string, next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Token") != want {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		role := r.Header.Get("X-Role")
		if !roleOK(role, allowed...) {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	}
}

func trimRole(r *http.Request) string {
	return strings.TrimSpace(r.Header.Get("X-Role"))
}
