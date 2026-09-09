package main

import (
	"github.com/wang550301463/sunny-knowledge/services/go/internal/gateway"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"log"
	"os"
	"strings"
	"time"
)

func main() {
	security, err := platform.LoadServiceSecurity()
	if err != nil {
		log.Fatal(err)
	}
	limits, err := gateway.RateLimitsFromEnv()
	if err != nil {
		log.Fatal("gateway rate limit configuration invalid")
	}
	limiter, err := gateway.NewValkeyLimiter(os.Getenv("VALKEY_URL"), "knowledge:gateway:limit:v1", 250*time.Millisecond)
	if err != nil {
		log.Fatal("gateway Valkey configuration invalid")
	}
	defer limiter.Close()
	routes := map[string]string{}
	for _, service := range []string{"iam", "knowledge", "ingest", "retrieval", "llm", "graphiti", "agent", "mcp", "channel"} {
		routes[service] = platform.Env(strings.ToUpper(service)+"_URL", "http://"+service+":8080")
	}
	handler, err := gateway.NewHandler(gateway.Config{AuthURL: platform.Env("AUTH_URL", "http://auth:8080"), KeycloakURL: platform.Env("KEYCLOAK_PROXY_URL", "http://keycloak:8080"), WebURL: platform.Env("WEB_URL", "http://web:8080"), Routes: routes, Limiter: limiter, Limits: limits}, security)
	if err != nil {
		log.Fatal(err)
	}
	if err = platform.Serve(handler); err != nil {
		log.Fatal(err)
	}
}
