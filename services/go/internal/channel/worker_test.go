package channel

import (
	"context"
	"encoding/json"
	"github.com/gorilla/websocket"
	"sync/atomic"
	"testing"
	"time"
)

type testAgent struct {
	calls  atomic.Int32
	cancel atomic.Int32
	last   RunContext
}

func (a *testAgent) Execute(ctx context.Context, v RunContext, q, k string, emit func(Update) error) (string, error) {
	a.calls.Add(1)
	a.last = v
	if e := emit(Update{Content: "依据", RunID: "run"}); e != nil {
		return "run", e
	}
	return "run", emit(Update{Content: "依据代码确认", Finished: true, RunID: "run"})
}
func (a *testAgent) Cancel(context.Context, RunContext) error { a.cancel.Add(1); return nil }
func (a *testAgent) Clear(context.Context, RunContext) error  { return nil }
func TestPostgresWebSocketWorkerDuplicateAndUnboundHelp(t *testing.T) {
	s := integrationStore(t)
	c := enabled(t, s)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	l, _ := s.Acquire(ctx, c.ID, "bind", time.Minute)
	ch, _ := s.BeginChallenge(ctx, l, "u")
	p, _ := s.ClaimChallenge(ctx, "alice", ch.ID, ch.WebToken)
	if e := s.ConfirmChallenge(ctx, l, "u", ch.ID, p); e != nil {
		t.Fatal(e)
	}
	s.Release(ctx, l)
	finished := make(chan struct{}, 4)
	url := simulator(t, func(ws *websocket.Conn) {
		var f Frame
		if ws.ReadJSON(&f) != nil {
			return
		}
		ack(ws, f)
		for _, id := range []string{"m", "m", "unbound"} {
			u := "u"
			if id == "unbound" {
				u = "new"
			}
			body, _ := json.Marshal(map[string]any{"msgid": id, "aibotid": "bot", "chattype": "single", "from": map[string]string{"userid": u}, "msgtype": "text", "text": map[string]string{"content": "question"}})
			ws.WriteJSON(Frame{Command: "aibot_msg_callback", Headers: Headers{id}, Body: body})
		}
		for {
			if ws.ReadJSON(&f) != nil {
				return
			}
			ack(ws, f)
			var b struct {
				Stream struct {
					Finish bool `json:"finish"`
				} `json:"stream"`
			}
			json.Unmarshal(f.Body, &b)
			if b.Stream.Finish {
				finished <- struct{}{}
			}
		}
	})
	agent := &testAgent{}
	w := NewWorker(s, agent, WorkerOptions{Transport: TransportOptions{URL: url, AllowLoopback: true, AckTimeout: time.Second}, LeaseTTL: time.Second, PollInterval: 10 * time.Millisecond, WebURL: "https://knowledge.example"})
	done := make(chan error, 1)
	go func() { done <- w.Run(ctx) }()
	for i := 0; i < 2; i++ {
		select {
		case <-finished:
		case <-ctx.Done():
			t.Fatal("worker replies missing")
		}
	}
	cancel()
	<-done
	if agent.calls.Load() != 1 {
		t.Fatal("duplicate/unbound agent invocation", agent.calls.Load())
	}
	if agent.last.UserID != "alice" {
		t.Fatal("external identity used as actor")
	}
	var count int
	if e := s.Pool.QueryRow(context.Background(), "SELECT count(*) FROM channel_messages").Scan(&count); e != nil || count != 2 {
		t.Fatal("durable dedup", count, e)
	}
}
