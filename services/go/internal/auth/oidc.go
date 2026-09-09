package auth

import (
	"context"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"errors"
	"github.com/golang-jwt/jwt/v5"
	"io"
	"math/big"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

type UserToken struct {
	jwt.RegisteredClaims
	Scope           string `json:"scope"`
	Name            string `json:"preferred_username"`
	Email           string `json:"email"`
	AuthorizedParty string `json:"azp"`
}

func (u UserToken) HasScope(scope string) bool {
	for _, s := range strings.Fields(u.Scope) {
		if s == scope {
			return true
		}
	}
	return false
}

const (
	keyCacheTTL     = 5 * time.Minute
	refreshCooldown = time.Second
)

type Verifier struct {
	Issuer, InternalURL, Audience string
	HTTP                          *http.Client
	mu                            sync.Mutex
	keys                          map[string]*rsa.PublicKey
	loaded                        time.Time
	refreshing                    bool
	lastAttempt                   time.Time
	refreshDone                   chan struct{}
}

func NewVerifier(issuer, internal, audience string) *Verifier {
	if internal == "" {
		internal = issuer
	}
	return &Verifier{Issuer: strings.TrimRight(issuer, "/"), InternalURL: strings.TrimRight(internal, "/"), Audience: audience, HTTP: &http.Client{Timeout: 5 * time.Second, Transport: platform.TraceTransport{}}, keys: map[string]*rsa.PublicKey{}}
}
func (v *Verifier) fetch(ctx context.Context, endpoint string, out any) error {
	req, err := http.NewRequestWithContext(ctx, "GET", endpoint, nil)
	if err != nil {
		return err
	}
	resp, err := v.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return errors.New("OIDC endpoint unavailable")
	}
	return json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(out)
}
func (v *Verifier) refresh(ctx context.Context) error {
	var discovery struct {
		Issuer string `json:"issuer"`
		JWKS   string `json:"jwks_uri"`
	}
	if err := v.fetch(ctx, v.InternalURL+"/.well-known/openid-configuration", &discovery); err != nil {
		return err
	}
	if discovery.Issuer != v.Issuer {
		return errors.New("discovery issuer mismatch")
	}
	// Only the configured issuer's endpoints can be requested; discovery cannot redirect to arbitrary hosts.
	if !strings.HasPrefix(discovery.JWKS, v.Issuer+"/") {
		return errors.New("JWKS outside configured issuer")
	}
	endpoint := v.InternalURL + strings.TrimPrefix(discovery.JWKS, v.Issuer)
	var jwks struct {
		Keys []struct{ Kty, Use, Alg, Kid, N, E string } `json:"keys"`
	}
	if err := v.fetch(ctx, endpoint, &jwks); err != nil {
		return err
	}
	keys := map[string]*rsa.PublicKey{}
	for _, key := range jwks.Keys {
		if key.Kty != "RSA" || (key.Use != "" && key.Use != "sig") || (key.Alg != "" && key.Alg != "RS256") || key.Kid == "" {
			continue
		}
		n, err := base64.RawURLEncoding.DecodeString(key.N)
		if err != nil {
			continue
		}
		e, err := base64.RawURLEncoding.DecodeString(key.E)
		if err != nil || len(e) > 4 {
			continue
		}
		exponent := new(big.Int).SetBytes(e).Int64()
		if len(n) < 256 || exponent < 3 || exponent%2 == 0 {
			continue
		}
		keys[key.Kid] = &rsa.PublicKey{N: new(big.Int).SetBytes(n), E: int(exponent)}
	}
	if len(keys) == 0 {
		return errors.New("no usable signing keys")
	}
	v.mu.Lock()
	v.keys = keys
	v.loaded = time.Now()
	v.mu.Unlock()
	return nil
}

// key resolves a signing key. Cached hits never block behind provider I/O:
// the refresh fetch runs outside the mutex, concurrent refreshes collapse
// into a single flight, a short cooldown bounds unknown-kid retry pressure,
// and waiters honour their own context while a refresh is in flight.
func (v *Verifier) key(ctx context.Context, kid string) (*rsa.PublicKey, error) {
	for {
		v.mu.Lock()
		if key, ok := v.keys[kid]; ok && time.Since(v.loaded) <= keyCacheTTL {
			v.mu.Unlock()
			return key, nil
		}
		if v.refreshing {
			done := v.refreshDone
			v.mu.Unlock()
			select {
			case <-done:
			case <-ctx.Done():
				return nil, ctx.Err()
			}
			continue
		}
		if time.Since(v.lastAttempt) < refreshCooldown {
			v.mu.Unlock()
			return nil, errors.New("unknown signing key")
		}
		v.refreshing = true
		v.lastAttempt = time.Now()
		v.refreshDone = make(chan struct{})
		v.mu.Unlock()
		// The fetch outlives any single waiter's cancellation; other waiters
		// still need the outcome, so detach it from this caller's context.
		err := v.refresh(context.WithoutCancel(ctx))
		v.mu.Lock()
		v.refreshing = false
		close(v.refreshDone)
		v.mu.Unlock()
		if err != nil {
			return nil, err
		}
	}
}
func (v *Verifier) Verify(ctx context.Context, bearer string) (claims UserToken, err error) {
	if v.Issuer == "" || v.Audience == "" {
		return claims, errors.New("OIDC not configured")
	}
	if _, err := url.ParseRequestURI(v.Issuer); err != nil {
		return claims, errors.New("invalid issuer")
	}
	parts := strings.Fields(bearer)
	if len(parts) != 2 || !strings.EqualFold(parts[0], "Bearer") || len(parts[1]) > 32768 {
		return claims, errors.New("Bearer token required")
	}
	_, err = jwt.ParseWithClaims(parts[1], &claims, func(token *jwt.Token) (any, error) {
		kid, ok := token.Header["kid"].(string)
		if !ok || kid == "" {
			return nil, errors.New("signing key id required")
		}
		return v.key(ctx, kid)
	}, jwt.WithValidMethods([]string{"RS256"}), jwt.WithIssuer(v.Issuer), jwt.WithAudience(v.Audience), jwt.WithExpirationRequired(), jwt.WithIssuedAt())
	if err == nil && claims.Subject == "" {
		err = errors.New("subject required")
	}
	return
}
