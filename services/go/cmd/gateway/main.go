package main

import (
	"log"
	"strings"
	"time"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/gateway"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

func main() {
	security, err := platform.LoadServiceSecurity()
	if err != nil {
		log.Fatal(err)
	}
	routes := map[string]string{}
	for _, service := range []string{"iam", "knowledge", "ingest", "retrieval", "llm", "graphiti", "agent", "mcp", "channel"} {
		routes[service] = platform.Env(strings.ToUpper(service)+"_URL", "http://"+service+":8080")
	}
	config := gateway.Config{AuthURL: platform.Env("AUTH_URL", "http://auth:8080"), KeycloakURL: platform.Env("KEYCLOAK_PROXY_URL", "http://keycloak:8080"), WebURL: platform.Env("WEB_URL", "http://web:8080"), Routes: routes, Limits: gateway.DefaultRateLimits()}
	// Admission control is mandatory when a VALKEY_URL is configured; without
	// it the gateway serves but /readyz reports admission control unavailable.
	if endpoint := platform.Env("VALKEY_URL", ""); endpoint != "" {
		limiter, limiterErr := gateway.NewValkeyLimiter(endpoint, "sk-gateway", 2*time.Second)
		if limiterErr != nil {
			log.Fatal(limiterErr)
		}
		config.Limiter = limiter
	}
	handler, err := gateway.NewHandler(config, security)
	if err != nil {
		log.Fatal(err)
	}
	if err = platform.Serve(handler); err != nil {
		log.Fatal(err)
	}
}
