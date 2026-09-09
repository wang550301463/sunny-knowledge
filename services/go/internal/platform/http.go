package platform

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

type Principal struct {
	ID          string   `json:"id"`
	Subjects    []string `json:"subjects"`
	Permissions []string `json:"permissions"`
	AuthEpoch   int64    `json:"auth_epoch"`
}
type Resolved struct {
	Principal Principal `json:"principal"`
	Scopes    []string  `json:"scopes"`
	Audiences []string  `json:"audiences"`
	Issuer    string    `json:"issuer"`
	ClientID  string    `json:"client_id"`
	ExpiresAt int64     `json:"expires_at"`
	Delegated bool      `json:"delegated,omitempty"`
}
type Decision struct {
	Allowed    bool   `json:"allowed"`
	AuthEpoch  int64  `json:"auth_epoch"`
	ACLDomain  string `json:"acl_domain"`
	ACLVersion int64  `json:"acl_version"`
}
type Resource struct {
	SpaceID    string `json:"space_id"`
	ResourceID string `json:"resource_id,omitempty"`
}
type AuthorizationRequest struct {
	Action     string `json:"action"`
	SpaceID    string `json:"space_id"`
	ResourceID string `json:"resource_id,omitempty"`
}

func JSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
func Error(w http.ResponseWriter, r *http.Request, status int, code, message string) {
	JSON(w, status, map[string]any{"error": map[string]any{"code": code, "message": message, "request_id": w.Header().Get("X-Request-ID")}})
}
func Decode(w http.ResponseWriter, r *http.Request, v any) error {
	r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
	d := json.NewDecoder(r.Body)
	d.DisallowUnknownFields()
	if err := d.Decode(v); err != nil {
		return err
	}
	var extra any
	if err := d.Decode(&extra); err != io.EOF {
		return errors.New("one JSON value required")
	}
	return nil
}
func ID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	s := hex.EncodeToString(b[:])
	return s[:8] + "-" + s[8:12] + "-" + s[12:16] + "-" + s[16:20] + "-" + s[20:]
}
func Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Request-ID")
		if len(id) != 36 {
			id = ID()
		} else {
			if _, err := hex.DecodeString(strings.ReplaceAll(id, "-", "")); err != nil {
				id = ID()
			}
		}
		r = r.WithContext(WithRequestID(r.Context(), id))
		r.Header.Set("X-Request-ID", id)
		w.Header().Set("X-Request-ID", id)
		w.Header().Set("X-Content-Type-Options", "nosniff")
		next.ServeHTTP(w, r)
	})
}
func HasPermission(p Principal, permission string) bool {
	for _, v := range p.Permissions {
		if v == permission {
			return true
		}
	}
	return false
}
func Serve(handler http.Handler) error {
	server := &http.Server{Addr: ":8080", Handler: Middleware(handler), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 30 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 32 << 10}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
	}()
	err := server.ListenAndServe()
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}
func Env(name, fallback string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return fallback
}

type requestIDKey struct{}

func RequestID(ctx context.Context) string { v, _ := ctx.Value(requestIDKey{}).(string); return v }
func WithRequestID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, requestIDKey{}, id)
}
