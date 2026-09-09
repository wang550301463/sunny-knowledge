package platform

import (
	"context"
	"crypto/ed25519"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"github.com/golang-jwt/jwt/v5"
	"net/http"
	"os"
	"time"
)

type ServiceSecurity struct {
	private ed25519.PrivateKey
	public  map[string]ed25519.PublicKey
}
type callerKey struct{}

func NewServiceSecurity(private ed25519.PrivateKey, public map[string]ed25519.PublicKey) *ServiceSecurity {
	return &ServiceSecurity{private: private, public: public}
}
func LoadServiceSecurity() (*ServiceSecurity, error) {
	privateData, err := os.ReadFile(os.Getenv("SERVICE_PRIVATE_KEY_FILE"))
	if err != nil {
		return nil, errors.New("service private key unavailable")
	}
	block, _ := pem.Decode(privateData)
	if block == nil {
		return nil, errors.New("invalid private PEM")
	}
	key, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, err
	}
	private, ok := key.(ed25519.PrivateKey)
	if !ok {
		return nil, errors.New("Ed25519 private key required")
	}
	publicData, err := os.ReadFile(os.Getenv("SERVICE_PUBLIC_KEYS_FILE"))
	if err != nil {
		return nil, errors.New("service public registry unavailable")
	}
	var registry map[string]string
	if err = json.Unmarshal(publicData, &registry); err != nil {
		return nil, err
	}
	public := map[string]ed25519.PublicKey{}
	for id, value := range registry {
		block, _ := pem.Decode([]byte(value))
		if block == nil {
			return nil, errors.New("invalid public PEM")
		}
		key, err := x509.ParsePKIXPublicKey(block.Bytes)
		if err != nil {
			return nil, err
		}
		ed, ok := key.(ed25519.PublicKey)
		if !ok {
			return nil, errors.New("Ed25519 public key required")
		}
		public[id] = ed
	}
	return NewServiceSecurity(private, public), nil
}
func (s *ServiceSecurity) Mint(caller, target string) (string, error) {
	if len(s.private) != ed25519.PrivateKeySize || caller == "" || target == "" {
		return "", errors.New("invalid service signing configuration")
	}
	now := time.Now()
	token := jwt.NewWithClaims(jwt.SigningMethodEdDSA, jwt.RegisteredClaims{Issuer: "knowledge-services", Subject: caller, Audience: []string{target}, IssuedAt: jwt.NewNumericDate(now), ExpiresAt: jwt.NewNumericDate(now.Add(60 * time.Second))})
	token.Header["kid"] = caller
	return token.SignedString(s.private)
}
func (s *ServiceSecurity) Verify(token, target string) (string, error) {
	if len(s.public) == 0 {
		return "", errors.New("invalid service verification configuration")
	}
	claims := new(jwt.RegisteredClaims)
	_, err := jwt.ParseWithClaims(token, claims, func(t *jwt.Token) (any, error) {
		kid, _ := t.Header["kid"].(string)
		key, ok := s.public[kid]
		if !ok || kid != claims.Subject {
			return nil, errors.New("unknown or mismatched workload key")
		}
		return key, nil
	}, jwt.WithValidMethods([]string{"EdDSA"}), jwt.WithIssuer("knowledge-services"), jwt.WithAudience(target), jwt.WithExpirationRequired(), jwt.WithIssuedAt())
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
