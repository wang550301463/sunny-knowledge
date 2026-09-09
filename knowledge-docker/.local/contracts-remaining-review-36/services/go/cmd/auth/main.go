package main

import (
	"github.com/wang550301463/sunny-knowledge/services/go/internal/auth"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"log"
	"os"
)

func main() {
	security, err := platform.LoadServiceSecurity()
	if err != nil {
		log.Fatal(err)
	}
	if os.Getenv("KEYCLOAK_ISSUER") == "" || os.Getenv("KEYCLOAK_AUDIENCE") == "" {
		log.Fatal("KEYCLOAK_ISSUER and KEYCLOAK_AUDIENCE required")
	}
	verifier := auth.NewVerifier(os.Getenv("KEYCLOAK_ISSUER"), os.Getenv("KEYCLOAK_INTERNAL_URL"), os.Getenv("KEYCLOAK_AUDIENCE"))
	var options []func(*auth.Handler)
	if path := os.Getenv("AUTH_CHANNEL_DELEGATION_KEY_FILE"); path != "" {
		broker, err := auth.LoadDelegationBroker(path, platform.Env("CHANNEL_URL", "http://channel:8080"))
		if err != nil {
			log.Fatal(err)
		}
		options = append(options, auth.WithDelegation(broker))
	}
	handler := auth.NewHandler(verifier, platform.NewClient("auth", security), platform.Env("IAM_URL", "http://iam:8080"), security, options...)
	if err = platform.Serve(handler); err != nil {
		log.Fatal(err)
	}
}
