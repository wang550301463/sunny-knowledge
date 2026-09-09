package main

import (
	"context"
	"encoding/base64"
	"flag"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/channel"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	worker := flag.Bool("worker", false, "run the independent channel connection worker")
	flag.Parse()
	sec, e := platform.LoadServiceSecurity()
	if e != nil {
		log.Fatal("channel workload identity unavailable")
	}
	key, e := base64.StdEncoding.DecodeString(os.Getenv("CHANNEL_ENCRYPTION_KEY"))
	if e != nil {
		log.Fatal("channel encryption key invalid")
	}
	box, e := channel.NewSecretBox(key)
	if e != nil {
		log.Fatal("channel encryption key invalid")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	pool, e := pgxpool.New(ctx, os.Getenv("DATABASE_URL"))
	if e != nil {
		log.Fatal("channel database configuration invalid")
	}
	defer pool.Close()
	s := channel.NewStore(pool, box)
	if s.Migrate(ctx) != nil {
		log.Fatal("channel database migration failed")
	}
	cancel()
	client := platform.NewClient("channel", sec)
	auth := platform.Env("AUTH_URL", "http://auth:8080")
	agent := platform.Env("AGENT_URL", "http://agent:8080")
	iam := platform.Env("IAM_URL", "http://iam:8080")
	if *worker {
		ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
		defer cancel()
		w := channel.NewWorker(s, nil, channel.WorkerOptions{Transport: channel.TransportOptions{URL: platform.Env("CHANNEL_WS_URL", channel.DefaultWebSocketURL), AllowLoopback: os.Getenv("CHANNEL_ALLOW_LOOPBACK_WS") == "true"}, WebURL: os.Getenv("CHANNEL_WEB_URL")})
		if w.Run(ctx) != nil {
			log.Fatal("channel worker stopped: dependency unavailable")
		}
		return
	}
	h := channel.NewHandler(s, client, auth, sec, &channel.HTTPVerifier{Client: client, AgentURL: agent, IAMURL: iam})
	if platform.Serve(h) != nil {
		log.Fatal("channel API stopped")
	}
}
