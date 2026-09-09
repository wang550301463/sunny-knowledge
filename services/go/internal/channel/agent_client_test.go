package channel

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

func TestHTTPAgentClientUsesOpaqueContextAndRealIncrementalRunViews(t *testing.T) {
	pub,key,_:=ed25519.GenerateKey(rand.Reader)
	sec:=platform.NewServiceSecurity(key,map[string]ed25519.PublicKey{"channel":pub,"auth":pub,"agent":pub})
	ctx,cancel:=context.WithTimeout(context.Background(),5*time.Second);defer cancel()
	v:=RunContext{ID:"context",ChannelID:"bot",UserID:"alice",AgentID:"agent",AgentConfigurationID:"config",SpaceIDs:[]string{"space"}}
	var mu sync.Mutex
	status:="running";hidden:=false;var answer any
	streamReleased:=make(chan struct{});var release sync.Once
	defer release.Do(func(){close(streamReleased)})
	view:=func() map[string]any {return map[string]any{"id":"run","session_id":"session","entrypoint":"channel","agent_id":"agent","configuration_id":"config","actual_scope":[]string{"space"},"status":status,"event_seq":2,"content_hidden":hidden,"answer":answer,"answer_complete":status=="completed","citations":[]any{map[string]any{"id":"cite","space_id":"space","page_id":"page","revision_id":"revision","evidence":map[string]any{"path":"Main.java"}}}}}
	auth:=httptest.NewServer(sec.Middleware("auth",http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
		if platform.Caller(r.Context())!="channel" || r.URL.Path!="/internal/v1/channel-token" || r.Header.Get("Authorization")!="" {t.Error("invalid context exchange")}
		var body map[string]any;json.NewDecoder(r.Body).Decode(&body)
		if len(body)!=1||body["context_id"]!="context" {t.Error("caller JSON impersonation or context mismatch")}
		platform.JSON(w,200,map[string]any{"access_token":"skc_fixture","token_type":"Bearer","expires_in":170,"scope":"knowledge:read"})
	})));defer auth.Close()
	agent:=httptest.NewServer(sec.Middleware("agent",http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
		if platform.Caller(r.Context())!="channel" || r.Header.Get("Authorization")!="Bearer skc_fixture" {t.Error("missing channel delegation")}
		switch r.URL.Path {
		case "/internal/v1/channel/runs":
			var body map[string]any;json.NewDecoder(r.Body).Decode(&body)
			if len(body)!=1||body["question"]!="question" {t.Error("unexpected run authority in JSON")}
			mu.Lock();defer mu.Unlock();platform.JSON(w,201,view())
		case "/internal/v1/channel/runs/run":
			mu.Lock();defer mu.Unlock();platform.JSON(w,200,view())
		case "/internal/v1/channel/runs/run/events":
			w.Header().Set("Content-Type","text/event-stream")
			mu.Lock();answer=map[string]any{"facts":[]any{map[string]any{"text":"真实逐段回答","citation_ids":[]string{"cite"}}},"inferences":[]any{},"gaps":[]string{}};mu.Unlock()
			fmt.Fprint(w,"id: 1\\n			select {case <-streamReleased:case <-ctx.Done():return}
			mu.Lock();status="completed";mu.Unlock()
			fmt.Fprint(w,"id: 2\\n		case "/internal/v1/channel/conversation/cancel":platform.JSON(w,200,map[string]any{"cancelled":true,"run_id":"run"})
		case "/internal/v1/channel/conversation/clear":platform.JSON(w,200,map[string]any{"cleared":true})
		default:t.Errorf("unexpected path %s",r.URL.Path);w.WriteHeader(404)
		}
	})));defer agent.Close()
	client:=&HTTPAgentClient{Client:platform.NewClient("channel",sec),AuthURL:auth.URL,AgentURL:agent.URL,WebURL:"https://knowledge.example"}
	var updates []Update
	run,e:=client.Execute(ctx,v,"question","ignored-authority",func(update Update)error{
		if strings.Contains(update.Content,"DO_NOT_RENDER_EVENT_BODY") {t.Error("unverified event rendered")}
		if update.BeforeSend==nil {t.Fatal("update lacks live authorization")}
		if e:=update.BeforeSend(ctx);e!=nil{return e}
		updates=append(updates,update)
		if strings.Contains(update.Content,"真实逐段回答")&&!update.Finished {release.Do(func(){close(streamReleased)})}
		return nil
	})
	if e!=nil||run!="run"||len(updates)<2||!updates[len(updates)-1].Finished {t.Fatalf("incremental run failed %s %v (%d updates)",run,e,len(updates))}
	final:=updates[len(updates)-1]
	if !strings.Contains(final.Content,"https://knowledge.example/runs/run") {t.Fatal("missing protected citation link")}
	mu.Lock();hidden=true;mu.Unlock()
	if e=final.BeforeSend(ctx);e==nil {t.Fatal("cached final answer survived revocation")}
	if e=client.Cancel(ctx,v);e!=nil {t.Fatal(e)}
	if e=client.Clear(ctx,v);e!=nil {t.Fatal(e)}
}
