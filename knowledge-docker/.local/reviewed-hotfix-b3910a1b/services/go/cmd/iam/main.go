package main

import (
	"context"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/iam"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"log"
	"os"
	"time"
)

func main() {
	security, err := platform.LoadServiceSecurity()
	if err != nil {
		log.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	pool, err := pgxpool.New(ctx, os.Getenv("DATABASE_URL"))
	if err != nil {
		log.Fatal("database configuration invalid")
	}
	defer pool.Close()
	store := iam.NewStore(pool, os.Getenv("BOOTSTRAP_ADMIN_SUBJECT"))
	if err = store.Migrate(ctx); err != nil {
		log.Fatal("IAM migration failed")
	}
	handler := iam.NewHandler(store, platform.NewClient("iam", security), platform.Env("AUTH_URL", "http://auth:8080"), security)
	if err = platform.Serve(handler); err != nil {
		log.Fatal(err)
	}
}
