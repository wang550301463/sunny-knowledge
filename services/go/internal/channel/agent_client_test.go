package channel

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

func TestHTTPAgentClientUsesOpaqueContextAndRealIncrementalRunViews(t *testing.T) {
	pub, key, _ := ed25519.GenerateKey(rand.Reader)
	sec := platform.NewServiceSecurity(key, map[string]ed25519.PublicKey{"channel": pub, "auth": pub, "agent": pub})
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	v := RunContext{ID: "context", ChannelID: "bot", UserID: "alice", AgentID: "agent", AgentConfigurationID: "config", SpaceIDs: []string{"space"}}
	var mu sync.Mutex
	status := "running"
	hidden := false
	var answer any
	streamReleased := make(chan struct{})
	var release sync.Once
	defer release.Do(func() { close(streamReleased) })
	view := func() map[string]any {
		return map[string]any{"id": "run", "session_id": "session", "entrypoint": "channel", "agent_id": "agent", "configuration_id": "config", "actual_scope": []string{"space"}, "status": status, "event_seq": 2, "content_hidden": hidden, "answer": answer, "answer_complete": status == "completed", "citations": []any{map[string]any{"id": "cite", "space_id": "space", "page_id": "page", "revision_id": "revision", "evidence": map[string]any{"path": "Main.java"}}}}
	}
	auth := httptest.NewServer(sec.Middleware("auth", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if platform.Caller(r.Context()) != "channel" || r.URL.Path != "/internal/v1/channel-token" || r.Header.Get("Authorization") != "" {
			t.Error("invalid context exchange")
		}
		var body map[string]any
		json.NewDecoder(r.Body).Decode(&body)
		if len(body) != 1 || body["context_id"] != "context" {
			t.Error("caller JSON impersonation or context mismatch")
		}
		platform.JSON(w, 200, map[string]any{"access_token": "skc_fixture", "token_type": "Bearer", "expires_in": 170, "scope": "knowledge:read"})
	})))
	defer auth.Close()
	agent := httptest.NewServer(sec.Middleware("agent", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if platform.Caller(r.Context()) != "channel" || r.Header.Get("Authorization") != "Bearer skc_fixture" {
			t.Error("missing channel delegation")
		}
		switch r.URL.Path {
		case "/internal/v1/channel/runs":
			var body map[string]any
			json.NewDecoder(r.Body).Decode(&body)
			if len(body) != 1 || body["question"] != "question" {
				t.Error("unexpected run authority in JSON")
			}
			mu.Lock()
			defer mu.Unlock()
			platform.JSON(w, 201, view())
		case "/internal/v1/channel/runs/run":
			mu.Lock()
			defer mu.Unlock()
			platform.JSON(w, 200, view())
		case "/internal/v1/channel/runs/run/events":
			w.Header().Set("Content-Type", "text/event-stream")
			mu.Lock()
			answer = map[string]any{"facts": []any{map[string]any{"text": "真实逐段回答", "citation_ids": []string{"cite"}}}, "inferences": []any{}, "gaps": []string{}}
			mu.Unlock()
			fmt.Fprint(w, "id: 1\n\n")
			if f, ok := w.(http.Flusher); ok {
				f.Flush()
			}
			select {
			case <-streamReleased:
			case <-ctx.Done():
				return
			}
			mu.Lock()
			status = "completed"
			mu.Unlock()
			fmt.Fprint(w, "id: 2\n\n")
			if f, ok := w.(http.Flusher); ok {
				f.Flush()
			}
		case "/internal/v1/channel/conversation/cancel":
			platform.JSON(w, 200, map[string]any{"cancelled": true, "run_id": "run"})
		case "/internal/v1/channel/conversation/clear":
			platform.JSON(w, 200, map[string]any{"cleared": true})
		default:
			t.Errorf("unexpected path %s", r.URL.Path)
			w.WriteHeader(404)
		}
	})))
	defer agent.Close()
	client := &HTTPAgentClient{Client: platform.NewClient("channel", sec), AuthURL: auth.URL, AgentURL: agent.URL, WebURL: "https://knowledge.example"}
	var updates []Update
	run, e := client.Execute(ctx, v, "question", "ignored-authority", func(update Update) error {
		if strings.Contains(update.Content, "DO_NOT_RENDER_EVENT_BODY") {
			t.Error("unverified event rendered")
		}
		if update.BeforeSend == nil {
			t.Fatal("update lacks live authorization")
		}
		if e := update.BeforeSend(ctx); e != nil {
			return e
		}
		updates = append(updates, update)
		if strings.Contains(update.Content, "真实逐段回答") && !update.Finished {
			release.Do(func() { close(streamReleased) })
		}
		return nil
	})
	if e != nil || run != "run" || len(updates) < 2 || !updates[len(updates)-1].Finished {
		t.Fatalf("incremental run failed %s %v (%d updates)", run, e, len(updates))
	}
	final := updates[len(updates)-1]
	if !strings.Contains(final.Content, "https://knowledge.example/runs/run") {
		t.Fatal("missing protected citation link")
	}
	mu.Lock()
	hidden = true
	mu.Unlock()
	if e = final.BeforeSend(ctx); e == nil {
		t.Fatal("cached final answer survived revocation")
	}
	if e = client.Cancel(ctx, v); e != nil {
		t.Fatal(e)
	}
	if e = client.Clear(ctx, v); e != nil {
		t.Fatal(e)
	}
}

func clientRunFixture() map[string]any {
	return map[string]any{"id": "run", "session_id": "session", "entrypoint": "channel", "agent_id": "agent", "configuration_id": "config", "actual_scope": []string{"space"}, "status": "completed", "event_seq": 2, "content_hidden": false, "answer_complete": true,
		"answer":    map[string]any{"facts": []any{map[string]any{"text": "来源证明的事实", "citation_ids": []string{"cite"}}}, "inferences": []any{}, "gaps": []string{}},
		"citations": []any{map[string]any{"id": "cite", "space_id": "space", "page_id": "page", "revision_id": "revision", "evidence": map[string]any{"path": "Main.java"}, "url": "https://untrusted.invalid/raw"}}}
}

func clientHTTPFixture(t *testing.T, tokenBody any, handler http.HandlerFunc) (*HTTPAgentClient, RunContext) {
	t.Helper()
	pub, private, _ := ed25519.GenerateKey(rand.Reader)
	security := platform.NewServiceSecurity(private, map[string]ed25519.PublicKey{"channel": pub})
	auth := httptest.NewServer(security.Middleware("auth", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || r.URL.Path != "/internal/v1/channel-token" || r.Header.Get("Authorization") != "" {
			t.Error("unexpected token exchange authority")
		}
		if tokenBody == nil {
			platform.JSON(w, 200, map[string]any{"access_token": "skc_fixture", "token_type": "Bearer", "scope": "knowledge:read", "expires_in": 170})
		} else {
			platform.JSON(w, 200, tokenBody)
		}
	})))
	agent := httptest.NewServer(security.Middleware("agent", handler))
	t.Cleanup(auth.Close)
	t.Cleanup(agent.Close)
	return &HTTPAgentClient{Client: platform.NewClient("channel", security), AuthURL: auth.URL, AgentURL: agent.URL, WebURL: "https://knowledge.example"}, RunContext{ID: "context", AgentID: "agent", AgentConfigurationID: "config", SpaceIDs: []string{"space"}}
}

func TestHTTPAgentClientRejectsMalformedOrBroaderRunViews(t *testing.T) {
	cases := map[string]func(map[string]any){
		"hidden":           func(v map[string]any) { v["content_hidden"] = true },
		"missing_guard":    func(v map[string]any) { delete(v, "content_hidden") },
		"null_guard":       func(v map[string]any) { v["content_hidden"] = nil },
		"web_entry":        func(v map[string]any) { v["entrypoint"] = "web" },
		"agent":            func(v map[string]any) { v["agent_id"] = "another" },
		"config":           func(v map[string]any) { v["configuration_id"] = "another" },
		"scope":            func(v map[string]any) { v["actual_scope"] = []string{"private"} },
		"empty_scope":      func(v map[string]any) { v["actual_scope"] = []string{} },
		"double_scope":     func(v map[string]any) { v["actual_scope"] = []string{"space", "space"} },
		"path":             func(v map[string]any) { v["id"] = "../run" },
		"no_session":       func(v map[string]any) { delete(v, "session_id") },
		"status":           func(v map[string]any) { v["status"] = "made_up" },
		"no_complete":      func(v map[string]any) { delete(v, "answer_complete") },
		"false_complete":   func(v map[string]any) { v["answer_complete"] = false },
		"no_answer":        func(v map[string]any) { v["answer"] = nil },
		"no_cursor":        func(v map[string]any) { delete(v, "event_seq") },
		"negative_cursor":  func(v map[string]any) { v["event_seq"] = -1 },
		"missing_citation": func(v map[string]any) { v["citations"] = []any{} },
		"source_scope":     func(v map[string]any) { v["citations"].([]any)[0].(map[string]any)["space_id"] = "private" },
		"null_evidence":    func(v map[string]any) { v["citations"].([]any)[0].(map[string]any)["evidence"] = nil },
		"empty_fact":       func(v map[string]any) { v["answer"].(map[string]any)["facts"].([]any)[0].(map[string]any)["text"] = "" },
		"invented_citation": func(v map[string]any) {
			v["answer"].(map[string]any)["facts"].([]any)[0].(map[string]any)["citation_ids"] = []string{"invented"}
		},
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			view := clientRunFixture()
			mutate(view)
			var calls atomic.Int32
			client, scope := clientHTTPFixture(t, nil, func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				platform.JSON(w, 201, view)
			})
			_, err := client.Execute(context.Background(), scope, "问题", "", func(Update) error { t.Error("invalid content escaped"); return nil })
			if err == nil || calls.Load() != 1 {
				t.Fatalf("invalid view accepted/retried: %v, %d", err, calls.Load())
			}
		})
	}
}

func TestHTTPAgentClientRejectsInvalidDelegationWithoutCallingAgent(t *testing.T) {
	for name, change := range map[string]any{"access_token": "skr_history", "token_type": "bearer", "scope": "knowledge:read knowledge:write", "expires_in": 181} {
		t.Run(name, func(t *testing.T) {
			body := map[string]any{"access_token": "skc_valid", "token_type": "Bearer", "scope": "knowledge:read", "expires_in": 170}
			body[name] = change
			client, scope := clientHTTPFixture(t, body, func(http.ResponseWriter, *http.Request) { t.Error("invalid token used") })
			if _, e := client.Execute(context.Background(), scope, "问题", "", func(Update) error { t.Error("invalid token emitted"); return nil }); e == nil {
				t.Fatal("invalid delegation accepted")
			}
		})
	}
}

func TestHTTPAgentClientRevalidatesExactClaimsAndContextAtSend(t *testing.T) {
	for _, change := range []string{"run", "session", "scope", "fact", "evidence", "status"} {
		t.Run(change, func(t *testing.T) {
			var mu sync.Mutex
			view := clientRunFixture()
			client, scope := clientHTTPFixture(t, nil, func(w http.ResponseWriter, r *http.Request) {
				mu.Lock()
				defer mu.Unlock()
				platform.JSON(w, 200, view)
			})
			var update Update
			_, e := client.Execute(context.Background(), scope, "问题", "", func(u Update) error { update = u; return u.BeforeSend(context.Background()) })
			if e != nil || strings.Contains(update.Content, "untrusted.invalid") {
				t.Fatalf("valid answer failed or upstream citation URL trusted: %v", e)
			}
			mu.Lock()
			switch change {
			case "run":
				view["id"] = "another"
			case "session":
				view["session_id"] = "another"
			case "scope":
				view["actual_scope"] = []string{"another"}
			case "fact":
				view["answer"].(map[string]any)["facts"].([]any)[0].(map[string]any)["text"] = "被替换的事实"
			case "evidence":
				view["citations"].([]any)[0].(map[string]any)["evidence"] = map[string]any{"path": "Another.java"}
			case "status":
				view["status"] = "partial"
				view["answer_complete"] = false
			}
			mu.Unlock()
			if e = update.BeforeSend(context.Background()); e == nil {
				t.Fatal("stale answer retained send authority")
			}
		})
	}
}

func TestHTTPAgentClientReconnectsReadStreamWithCursorWithoutCreatingAgain(t *testing.T) {
	var creates, streams atomic.Int32
	var mu sync.Mutex
	view := clientRunFixture()
	view["status"], view["answer_complete"], view["answer"] = "running", false, nil
	client, scope := clientHTTPFixture(t, nil, func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/events") {
			count := streams.Add(1)
			w.Header().Set("Content-Type", "text/event-stream")
			mu.Lock()
			view["answer"] = clientRunFixture()["answer"]
			if count == 2 {
				view["status"], view["answer_complete"] = "completed", true
			}
			mu.Unlock()
			if count == 1 {
				if r.Header.Get("Last-Event-ID") != "" {
					t.Error("initial cursor fabricated")
				}
				fmt.Fprint(w, "id: 1\nevent: answer_block\ndata: {\"seq\":1,\"type\":\"answer_block\"}\n\n")
			} else {
				if r.Header.Get("Last-Event-ID") != "1" {
					t.Error("last accepted cursor lost")
				}
				fmt.Fprint(w, "id: 1\nevent: answer_block\ndata: {\"seq\":1,\"type\":\"answer_block\"}\n\nid: 3\nevent: completed\ndata: {\"seq\":3,\"type\":\"completed\"}\n\n")
			}
			return
		}
		if r.Method == "POST" {
			creates.Add(1)
		}
		mu.Lock()
		defer mu.Unlock()
		platform.JSON(w, 200, view)
	})
	var updates []Update
	_, e := client.Execute(context.Background(), scope, "问题", "", func(u Update) error { updates = append(updates, u); return u.BeforeSend(context.Background()) })
	if e != nil || creates.Load() != 1 || streams.Load() != 2 || len(updates) != 3 || !updates[2].Finished {
		t.Fatalf("reconnect failed: %v, create=%d stream=%d updates=%d", e, creates.Load(), streams.Load(), len(updates))
	}
}

func TestHTTPAgentClientCancelsOpenStreamAndSendChecks(t *testing.T) {
	started := make(chan struct{})
	view := clientRunFixture()
	view["status"], view["answer_complete"] = "running", false
	client, scope := clientHTTPFixture(t, nil, func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/events") {
			w.Header().Set("Content-Type", "text/event-stream")
			w.(http.Flusher).Flush()
			close(started)
			<-r.Context().Done()
			return
		}
		platform.JSON(w, 200, view)
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { <-started; cancel() }()
	var update Update
	done := make(chan error, 1)
	go func() {
		_, e := client.Execute(ctx, scope, "问题", "", func(u Update) error { update = u; return u.BeforeSend(ctx) })
		done <- e
	}()
	select {
	case e := <-done:
		if !errors.Is(e, context.Canceled) {
			t.Fatalf("stream did not preserve cancellation: %v", e)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("stream remained blocked after cancellation")
	}
	if e := update.BeforeSend(ctx); e == nil {
		t.Fatal("cancelled send authorized")
	}
}

func TestHTTPAgentClientRejectsMalformedStreamAndNeverRetriesWrites(t *testing.T) {
	for _, frame := range []string{"event: error\ndata: {\"error\":{\"code\":\"denied\"}}\n\n", "id: 1\nevent: answer_block\ndata: {\"seq\":2,\"type\":\"answer_block\"}\n\n", "id: 1\nevent: answer_block\ndata: {\"seq\":1,\"type\":\"completed\"}\n\n", "id: 1\nid: 2\nevent: answer_block\ndata: {}\n\n"} {
		t.Run(frame, func(t *testing.T) {
			var creates, streams atomic.Int32
			view := clientRunFixture()
			view["status"], view["answer_complete"] = "running", false
			client, scope := clientHTTPFixture(t, nil, func(w http.ResponseWriter, r *http.Request) {
				if strings.HasSuffix(r.URL.Path, "/events") {
					streams.Add(1)
					w.Header().Set("Content-Type", "text/event-stream")
					fmt.Fprint(w, frame)
					return
				}
				if r.Method == "POST" {
					creates.Add(1)
				}
				platform.JSON(w, 200, view)
			})
			_, e := client.Execute(context.Background(), scope, "问题", "", func(u Update) error { return u.BeforeSend(context.Background()) })
			if e == nil || creates.Load() != 1 || streams.Load() != 1 {
				t.Fatalf("malformed stream accepted/retried: %v %d %d", e, creates.Load(), streams.Load())
			}
		})
	}
	for _, stage := range []string{"create_unknown", "send_unknown"} {
		t.Run(stage, func(t *testing.T) {
			var creates atomic.Int32
			client, scope := clientHTTPFixture(t, nil, func(w http.ResponseWriter, r *http.Request) {
				if r.Method == "POST" {
					creates.Add(1)
				}
				if stage == "create_unknown" {
					w.WriteHeader(503)
					return
				}
				platform.JSON(w, 200, clientRunFixture())
			})
			_, e := client.Execute(context.Background(), scope, "问题", "", func(Update) error { return ErrDeliveryUnknown })
			if e == nil || creates.Load() != 1 {
				t.Fatalf("uncertain operation retried: %v %d", e, creates.Load())
			}
		})
	}
}

func TestHTTPAgentClientCommandsRequireExplicitAcknowledgement(t *testing.T) {
	for _, command := range []string{"cancel", "clear"} {
		for _, body := range []string{"{}", "null", "{\"cancelled\":true}", "{\"cleared\":false}"} {
			t.Run(command+body, func(t *testing.T) {
				var calls atomic.Int32
				client, scope := clientHTTPFixture(t, nil, func(w http.ResponseWriter, r *http.Request) { calls.Add(1); fmt.Fprint(w, body) })
				var e error
				if command == "cancel" {
					e = client.Cancel(context.Background(), scope)
				} else {
					e = client.Clear(context.Background(), scope)
				}
				if e == nil || calls.Load() != 1 {
					t.Fatal("unacknowledged command accepted/retried")
				}
			})
		}
	}
}
