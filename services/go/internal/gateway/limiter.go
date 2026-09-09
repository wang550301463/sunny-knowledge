package gateway

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/valkey-io/valkey-go"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

var errLimiterUnavailable = errors.New("rate limiter unavailable")

type WindowLimit struct {
	Count  int
	Window time.Duration
}
type RateLimits struct{ Peer, IdentityPeer, Principal WindowLimit }

func DefaultRateLimits() RateLimits {
	return RateLimits{WindowLimit{600, time.Minute}, WindowLimit{120, time.Minute}, WindowLimit{300, time.Minute}}
}
func (v WindowLimit) valid() bool {
	return v.Count > 0 && v.Count <= 1000000 && v.Window >= time.Millisecond && v.Window <= time.Hour
}
func (v RateLimits) valid() bool {
	return v.Peer.valid() && v.IdentityPeer.valid() && v.Principal.valid()
}
func RateLimitsFromEnv() (RateLimits, error) {
	v := DefaultRateLimits()
	for key, field := range map[string]*int{"GATEWAY_PEER_LIMIT": &v.Peer.Count, "GATEWAY_IDP_LIMIT": &v.IdentityPeer.Count, "GATEWAY_PRINCIPAL_LIMIT": &v.Principal.Count} {
		if raw := os.Getenv(key); raw != "" {
			n, e := strconv.Atoi(raw)
			if e != nil {
				return v, errors.New("invalid rate limit configuration")
			}
			*field = n
		}
	}
	if raw := os.Getenv("GATEWAY_LIMIT_WINDOW"); raw != "" {
		d, e := time.ParseDuration(raw)
		if e != nil {
			return v, errors.New("invalid rate window")
		}
		v.Peer.Window = d
		v.IdentityPeer.Window = d
		v.Principal.Window = d
	}
	if !v.valid() {
		return v, errors.New("invalid rate limit configuration")
	}
	return v, nil
}

type Limiter interface {
	Allow(context.Context, string, WindowLimit) (time.Duration, error)
	Ready(context.Context) error
}
type ValkeyLimiter struct {
	options   valkey.ClientOption
	namespace string
	timeout   time.Duration
	gate      chan struct{}
	client    valkey.Client
	closed    bool
}

func NewValkeyLimiter(endpoint, namespace string, timeout time.Duration) (*ValkeyLimiter, error) {
	u, e := url.Parse(endpoint)
	if e != nil || u.Host == "" || u.RawQuery != "" || (u.Scheme != "redis" && u.Scheme != "rediss" && u.Scheme != "valkey" && u.Scheme != "valkeys") || namespace == "" || len(namespace) > 128 || timeout < time.Millisecond || timeout > 5*time.Second {
		return nil, errors.New("invalid rate limiter configuration")
	}
	opt, e := valkey.ParseURL(endpoint)
	if e != nil {
		return nil, errors.New("invalid rate limiter configuration")
	}
	opt.Dialer = net.Dialer{Timeout: timeout, KeepAlive: time.Second}
	opt.ConnWriteTimeout = timeout
	opt.DisableRetry = true
	opt.DisableCache = true
	opt.AlwaysPipelining = true
	return &ValkeyLimiter{options: opt, namespace: namespace, timeout: timeout, gate: make(chan struct{}, 1)}, nil
}
func (l *ValkeyLimiter) connection(ctx context.Context) (valkey.Client, error) {
	select {
	case l.gate <- struct{}{}:
		defer func() { <-l.gate }()
	case <-ctx.Done():
		return nil, errLimiterUnavailable
	}
	if l.closed || ctx.Err() != nil {
		return nil, errLimiterUnavailable
	}
	if l.client == nil {
		c, e := valkey.NewClient(l.options)
		if e != nil {
			return nil, errLimiterUnavailable
		}
		l.client = c
	}
	return l.client, nil
}
func (l *ValkeyLimiter) Close() {
	l.gate <- struct{}{}
	defer func() { <-l.gate }()
	l.closed = true
	if l.client != nil {
		l.client.Close()
	}
}
func (l *ValkeyLimiter) Ready(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, l.timeout)
	defer cancel()
	c, e := l.connection(ctx)
	if e != nil {
		return e
	}
	if c.Do(ctx, c.B().Ping().Build()).Error() != nil {
		return errLimiterUnavailable
	}
	return nil
}

// The first accepted request starts a fixed window. Rejections never extend its TTL.
// No blind retry: a timed-out command may have consumed an allowance.
const fixedWindowScript = `
local value = redis.call('GET', KEYS[1])
local ttl = redis.call('PTTL', KEYS[1])
local count = tonumber(value or '0')
if not count or count < 0 or (value and ttl < 0) then return redis.error_reply('invalid bucket') end
if count >= tonumber(ARGV[1]) then return math.max(1, ttl) end
local next = redis.call('INCR', KEYS[1])
if next == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[2]) end
return 0
`

func (l *ValkeyLimiter) bucket(key string) string {
	sum := sha256.Sum256([]byte(key))
	return l.namespace + ":" + hex.EncodeToString(sum[:])
}
func (l *ValkeyLimiter) Allow(ctx context.Context, key string, limit WindowLimit) (time.Duration, error) {
	if !limit.valid() {
		return 0, errLimiterUnavailable
	}
	ctx, cancel := context.WithTimeout(ctx, l.timeout)
	defer cancel()
	c, e := l.connection(ctx)
	if e != nil {
		return 0, e
	}
	retry, e := c.Do(ctx, c.B().Eval().Script(fixedWindowScript).Numkeys(1).Key(l.bucket(key)).Arg(strconv.Itoa(limit.Count), strconv.FormatInt(limit.Window.Milliseconds(), 10)).Build()).AsInt64()
	if e != nil || retry < 0 || retry > limit.Window.Milliseconds() {
		return 0, errLimiterUnavailable
	}
	return time.Duration(retry) * time.Millisecond, nil
}
func peerAddress(r *http.Request) (string, error) {
	host, _, e := net.SplitHostPort(r.RemoteAddr)
	if e != nil {
		return "", errLimiterUnavailable
	}
	ip, e := netip.ParseAddr(host)
	if e != nil {
		return "", errLimiterUnavailable
	}
	return ip.Unmap().String(), nil
}
func rateClass(path string) string {
	switch {
	case strings.HasPrefix(path, "/api/"):
		return "api"
	case path == "/mcp" || strings.HasPrefix(path, "/mcp/") || strings.HasPrefix(path, "/.well-known/"):
		return "mcp"
	case path == "/idp" || strings.HasPrefix(path, "/idp/"):
		return "idp"
	}
	return ""
}
func checkLimit(w http.ResponseWriter, r *http.Request, l Limiter, key string, limit WindowLimit) bool {
	retry, e := l.Allow(r.Context(), key, limit)
	if e != nil {
		platform.Error(w, r, 503, "rate_limit_unavailable", "Admission control unavailable")
		return false
	}
	if retry > 0 {
		w.Header().Set("Retry-After", strconv.FormatInt((retry.Milliseconds()+999)/1000, 10))
		w.Header().Set("Cache-Control", "no-store")
		platform.Error(w, r, 429, "rate_limited", "Request limit exceeded")
		return false
	}
	return true
}
