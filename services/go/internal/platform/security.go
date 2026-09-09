package platform

import (
	"context"
	"errors"
	"fmt"
	"github.com/golang-jwt/jwt/v5"
	"net/http"
	"time"
)

type ServiceSecurity struct{ secret []byte }
type callerKey struct{}

func NewServiceSecurity(secret string) *ServiceSecurity {
	return &ServiceSecurity{secret: []byte(secret)}
}
func (s *ServiceSecurity) Mint(caller, target string) (string, error) {
	if len(s.secret) < 32 || caller == "" || target == "" {
		return "", errors.New("invalid service signing configuration")
	}
	now := time.Now()
	return jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.RegisteredClaims{Issuer: "knowledge-services", Subject: caller, Audience: []string{target}, IssuedAt: jwt.NewNumericDate(now), ExpiresAt: jwt.NewNumericDate(now.Add(60 * time.Second))}).SignedString(s.secret)
}
func (s *ServiceSecurity) Verify(token, target string) (string, error) {
	if len(s.secret) < 32 {
		return "", errors.New("invalid service signing configuration")
	}
	claims := new(jwt.RegisteredClaims)
	_, err := jwt.ParseWithClaims(token, claims, func(t *jwt.Token) (any, error) { return s.secret, nil }, jwt.WithValidMethods([]string{"HS256"}), jwt.WithIssuer("knowledge-services"), jwt.WithAudience(target), jwt.WithExpirationRequired(), jwt.WithIssuedAt())
	if err != nil {
		return "", err
	}
	if claims.Subject == "" || claims.IssuedAt == nil || claims.ExpiresAt == nil || claims.ExpiresAt.Time.Sub(claims.IssuedAt.Time) > 60*time.Second || claims.ExpiresAt.Time.Before(claims.IssuedAt.Time) {
		return "", errors.New("invalid service identity or lifetime")
	}
	return claims.Subject, nil
}
func (s *ServiceSecurity) Middleware(target string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		caller, err := s.Verify(r.Header.Get("X-Service-Token"), target)
		if err != nil {
			Error(w, r, 401, "unauthorized", "Valid service identity required")
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), callerKey{}, caller)))
	})
}
func Caller(ctx context.Context) string { v, _ := ctx.Value(callerKey{}).(string); return v }
func RequireCaller(r *http.Request, allowed ...string) error {
	for _, v := range allowed {
		if Caller(r.Context()) == v {
			return nil
		}
	}
	return fmt.Errorf("caller denied")
}
