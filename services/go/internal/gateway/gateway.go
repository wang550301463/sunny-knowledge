package gateway

import (
	"context"
	"errors"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"time"
)

type Config struct {
	AuthURL, KeycloakURL, WebURL string
	Routes                       map[string]string
}

var domains = map[string]string{"me": "iam", "spaces": "iam", "groups": "iam", "departments": "iam", "users": "iam", "grants": "iam", "audit": "iam", "pages": "knowledge", "reviews": "knowledge", "revisions": "knowledge", "sources": "ingest", "tasks": "ingest", "search": "retrieval", "traverse": "retrieval", "timeline": "retrieval", "models": "llm", "agents": "agent", "sessions": "agent", "runs": "agent", "feedback": "agent", "channels": "channel", "bindings": "channel"}

func stripHeaders(h http.Header) {
	for name := range h {
		upper := strings.ToUpper(name)
		for _, prefix := range []string{"X-PRINCIPAL", "X-USER", "X-ROLE", "X-SERVICE", "X-INTERNAL", "X-FORWARDED"} {
			if strings.HasPrefix(upper, prefix) {
				h.Del(name)
				break
			}
		}
	}
	h.Del("Forwarded")
	h.Del("X-Request-ID")
}
func NewHandler(config Config, security *platform.ServiceSecurity) (http.Handler, error) {
	client := platform.NewClient("gateway", security)
	proxies := map[string]*httputil.ReverseProxy{}
	endpoints := map[string]string{}
	for name, endpoint := range config.Routes {
		endpoints[name] = endpoint
	}
	if config.KeycloakURL != "" {
		endpoints["keycloak"] = config.KeycloakURL
	}
	if config.WebURL != "" {
		endpoints["web"] = config.WebURL
	}
	for name, endpoint := range endpoints {
		target, err := url.Parse(endpoint)
		if err != nil || target.Host == "" || (target.Scheme != "http" && target.Scheme != "https") {
			return nil, errors.New("invalid upstream URL")
		}
		service := name
		proxies[name] = &httputil.ReverseProxy{Rewrite: func(p *httputil.ProxyRequest) {
			p.SetURL(target)
			p.SetXForwarded()
			p.Out.Header.Set("X-Request-ID", p.In.Header.Get("X-Request-ID"))
			if service != "keycloak" && service != "web" {
				token, err := security.Mint("gateway", service)
				if err == nil {
					p.Out.Header.Set("X-Service-Token", token)
				}
			}
		}, Transport: &http.Transport{Proxy: http.ProxyFromEnvironment, DialContext: (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext, ForceAttemptHTTP2: true, MaxIdleConns: 100, MaxIdleConnsPerHost: 20, IdleConnTimeout: 90 * time.Second, TLSHandshakeTimeout: 5 * time.Second, ResponseHeaderTimeout: 30 * time.Second}, FlushInterval: -1, ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			if r.Context().Err() == nil {
				platform.Error(w, r, 502, "upstream_unavailable", "Service unavailable")
			}
		}}
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		stripHeaders(r.Header)
		requestID := platform.ID()
		r.Header.Set("X-Request-ID", requestID)
		w.Header().Set("X-Request-ID", requestID)
		w.Header().Set("X-Content-Type-Options", "nosniff")
		if r.URL.Path == "/healthz" {
			platform.JSON(w, 200, map[string]string{"status": "ok"})
			return
		}
		service := ""
		authenticate := false
		switch {
		case r.URL.Path == "/idp" || strings.HasPrefix(r.URL.Path, "/idp/"):
			service = "keycloak"
		case r.URL.Path == "/mcp" || strings.HasPrefix(r.URL.Path, "/mcp/"):
			service = "mcp"
			authenticate = true
		case strings.HasPrefix(r.URL.Path, "/.well-known/"):
			service = "mcp"
		case strings.HasPrefix(r.URL.Path, "/api/"):
			parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/api/v1/"), "/")
			service = domains[parts[0]]
			authenticate = true
		default:
			service = "web"
		}
		proxy, exists := proxies[service]
		if service == "" || !exists {
			platform.Error(w, r, 404, "not_found", "Route not found")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 210*time.Second)
		defer cancel()
		r = r.WithContext(ctx)
		if r.ContentLength > 8<<20 {
			platform.Error(w, r, 413, "request_too_large", "Request body exceeds 8 MiB")
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, 8<<20)
		if authenticate {
			_, err := client.Resolve(ctx, config.AuthURL, r.Header.Get("Authorization"))
			if err != nil {
				status := 503
				code := "authorization_unavailable"
				var upstream *platform.HTTPError
				if errors.As(err, &upstream) && (upstream.Status == 401 || upstream.Status == 403) {
					status = upstream.Status
					code = "unauthorized"
				}
				if status == 401 {
					w.Header().Set("WWW-Authenticate", `Bearer resource_metadata="/.well-known/oauth-protected-resource"`)
				}
				platform.Error(w, r, status, code, "Valid active user authorization required")
				return
			}
		}
		proxy.ServeHTTP(w, r)
	}), nil
}
