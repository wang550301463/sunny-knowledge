package channel

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/gorilla/websocket"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

func simulator(t *testing.T, handler func(*websocket.Conn)) string {
	t.Helper()
	up := websocket.Upgrader{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, e := up.Upgrade(w, r, nil)
		if e != nil {
			return
		}
		defer c.Close()
		handler(c)
	}))
	t.Cleanup(srv.Close)
	return "ws" + strings.TrimPrefix(srv.URL, "http")
}
func ack(c *websocket.Conn, f Frame) {
	zero := 0
	c.WriteJSON(Frame{Headers: f.Headers, ErrorCode: &zero})
}
func TestWebSocketHandshakeCumulativeSerialAndFinish(t *testing.T) {
	var mu sync.Mutex
	var got []Frame
	url := simulator(t, func(c *websocket.Conn) {
		for {
			var f Frame
			if c.ReadJSON(&f) != nil {
				return
			}
			mu.Lock()
			got = append(got, f)
			mu.Unlock()
			ack(c, f)
		}
	})
	ctx, cancel := context.WithTimeout(context.Background(), time.Second*3)
	defer cancel()
	c, e := Dial(ctx, TransportOptions{URL: url, AllowLoopback: true, AckTimeout: time.Second}, "bot", "secret", nil)
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	for i, text := range []string{"你", "你好", "你好，世界"} {
		if e = c.Reply(ctx, responseFrame("req", "stream", text, i == 2)); e != nil {
			t.Fatal(e)
		}
	}
	mu.Lock()
	defer mu.Unlock()
	if len(got) != 4 || got[0].Command != "aibot_subscribe" {
		t.Fatal("handshake/replies missing")
	}
	var auth struct {
		BotID  string `json:"bot_id"`
		Secret string `json:"secret"`
	}
	json.Unmarshal(got[0].Body, &auth)
	if auth.BotID != "bot" || auth.Secret != "secret" {
		t.Fatal("bad auth")
	}
	var last struct {
		Stream struct {
			Content string `json:"content"`
			Finish  bool   `json:"finish"`
		} `json:"stream"`
	}
	json.Unmarshal(got[3].Body, &last)
	if !last.Stream.Finish || last.Stream.Content != "你好，世界" {
		t.Fatal("not cumulative/final")
	}
}
func TestWebSocketUnknownACKClosesAndNeverResends(t *testing.T) {
	count := 0
	url := simulator(t, func(c *websocket.Conn) {
		for {
			var f Frame
			if c.ReadJSON(&f) != nil {
				return
			}
			if f.Command == "aibot_subscribe" {
				ack(c, f)
			} else {
				count++
			}
		}
	})
	ctx := context.Background()
	c, e := Dial(ctx, TransportOptions{URL: url, AllowLoopback: true, AckTimeout: 30 * time.Millisecond}, "bot", "secret", nil)
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	if e = c.Reply(ctx, responseFrame("r", "s", "answer", true)); !errors.Is(e, ErrDeliveryUnknown) {
		t.Fatal("unacknowledged delivery reported success", e)
	}
	if e = c.Reply(ctx, responseFrame("r", "s", "answer", true)); e == nil {
		t.Fatal("connection reused after uncertain ACK")
	}
	if count != 1 {
		t.Fatal("blind retry", count)
	}
}
func TestWebSocketFencingAndServerDisplacement(t *testing.T) {
	url := simulator(t, func(c *websocket.Conn) {
		var f Frame
		c.ReadJSON(&f)
		ack(c, f)
		c.WriteJSON(Frame{Command: "aibot_event_callback", Headers: Headers{"event"}, Body: json.RawMessage(`{"event":{"eventtype":"disconnected_event"}}`)})
		time.Sleep(50 * time.Millisecond)
	})
	c, e := Dial(context.Background(), TransportOptions{URL: url, AllowLoopback: true, AckTimeout: time.Second}, "bot", "secret", nil)
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	select {
	case <-c.Done():
		if !errors.Is(c.Err(), ErrDisplaced) {
			t.Fatal(c.Err())
		}
	case <-time.After(time.Second):
		t.Fatal("displaced socket alive")
	}
	if _, e = Dial(context.Background(), TransportOptions{URL: url, AllowLoopback: true}, "bot", "secret", func(context.Context, func() error) error { return ErrLeaseLost }); !errors.Is(e, ErrLeaseLost) {
		t.Fatal("unfenced auth", e)
	}
}
