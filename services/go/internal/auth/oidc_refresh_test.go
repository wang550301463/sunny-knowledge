package auth

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"fmt"
	"github.com/golang-jwt/jwt/v5"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"math/big"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type refreshFixture struct {
	verifier    *Verifier
	key         *rsa.PrivateKey
	issuer      string
	requests    atomic.Int64
	block       atomic.Bool
	kid         atomic.Value
	started     chan struct{}
	release     chan struct{}
	once        sync.Once
	releaseOnce sync.Once
}

func newRefreshFixture(t *testing.T) *refreshFixture {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	f := &refreshFixture{key: key, started: make(chan struct{}), release: make(chan struct{})}
	f.kid.Store("current")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/.well-known/openid-configuration" {
			f.requests.Add(1)
			if f.block.Load() {
				f.once.Do(func() { close(f.started) })
				select {
				case <-f.release:
				case <-r.Context().Done():
					return
				}
			}
			platform.JSON(w, 200, map[string]string{"issuer": f.issuer, "jwks_uri": f.issuer + "/jwks"})
			return
		}
		platform.JSON(w, 200, map[string]any{"keys": []any{map[string]string{"kty": "RSA", "alg": "RS256", "kid": f.kid.Load().(string), "n": base64.RawURLEncoding.EncodeToString(key.N.Bytes()), "e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(key.E)).Bytes())}}})
	}))
	f.issuer = server.URL
	f.verifier = NewVerifier(server.URL, server.URL, "knowledge-web")
	t.Cleanup(func() { f.releaseOnce.Do(func() { close(f.release) }); server.Close() })
	return f
}
func (f *refreshFixture) token(kid string) string {
	token := jwt.NewWithClaims(jwt.SigningMethodRS256, jwt.MapClaims{"iss": f.issuer, "aud": "knowledge-web", "sub": "alice", "exp": time.Now().Add(time.Minute).Unix(), "iat": time.Now().Unix()})
	token.Header["kid"] = kid
	raw, _ := token.SignedString(f.key)
	return "Bearer " + raw
}
func (f *refreshFixture) prime(t *testing.T) {
	t.Helper()
	if _, err := f.verifier.Verify(context.Background(), f.token("current")); err != nil {
		t.Fatal(err)
	}
}

func TestOIDCUnknownRefreshDoesNotBlockCachedKeyOrCancelledWaiter(t *testing.T) {
	f := newRefreshFixture(t)
	f.prime(t)
	// Allow the short refresh cooldown to pass before simulating a stalled provider.
	time.Sleep(1100 * time.Millisecond)
	f.block.Store(true)
	first := make(chan error, 1)
	go func() { _, err := f.verifier.Verify(context.Background(), f.token("attacker-one")); first <- err }()
	select {
	case <-f.started:
	case <-time.After(time.Second):
		t.Fatal("unknown kid did not start refresh")
	}
	cached := make(chan error, 1)
	go func() { _, err := f.verifier.Verify(context.Background(), f.token("current")); cached <- err }()
	cancelled := make(chan error, 1)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	go func() { _, err := f.verifier.Verify(ctx, f.token("attacker-two")); cancelled <- err }()
	select {
	case err := <-cached:
		if err != nil {
			t.Errorf("cached key rejected while refreshing: %v", err)
		}
	case <-time.After(100 * time.Millisecond):
		t.Error("cached valid token blocked behind provider I/O")
	}
	select {
	case err := <-cancelled:
		if err == nil {
			t.Error("cancelled waiter accepted token")
		}
	case <-time.After(100 * time.Millisecond):
		t.Error("refresh waiter ignored its cancelled context")
	}
	f.releaseOnce.Do(func() { close(f.release) })
	select {
	case <-first:
	case <-time.After(time.Second):
		t.Fatal("refresh did not finish")
	}
}

func TestOIDCUnknownKidsHaveBoundedRefreshAndRotationRecovers(t *testing.T) {
	f := newRefreshFixture(t)
	f.prime(t)
	time.Sleep(1100 * time.Millisecond)
	for i := 0; i < 12; i++ {
		if _, err := f.verifier.Verify(context.Background(), f.token(fmt.Sprintf("attacker-%d", i))); err == nil {
			t.Fatal("unknown key accepted")
		}
	}
	if n := f.requests.Load(); n > 2 {
		t.Fatalf("unknown kids triggered %d discovery fetches, want at most initial+one refresh", n)
	}
	f.kid.Store("rotated")
	time.Sleep(1100 * time.Millisecond)
	if _, err := f.verifier.Verify(context.Background(), f.token("rotated")); err != nil {
		t.Fatalf("legitimate rotated key did not recover after cooldown: %v", err)
	}
	if _, err := f.verifier.Verify(context.Background(), f.token("current")); err == nil {
		t.Fatal("removed signing key remained accepted")
	}
}
