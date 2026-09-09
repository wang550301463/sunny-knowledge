package channel

import (
	"context"
	"encoding/json"
	"github.com/gorilla/websocket"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"net"
	"net/url"
	"sync"
	"time"
)

type TransportOptions struct {
	URL           string
	AllowLoopback bool
	AckTimeout    time.Duration
	Heartbeat     time.Duration
	WriteTimeout  time.Duration
}
type FenceWrite func(context.Context, func() error) error
type Connection struct {
	socket   *websocket.Conn
	options  TransportOptions
	fence    FenceWrite
	writes   sync.Mutex
	serial   sync.Mutex
	mu       sync.Mutex
	pending  map[string]chan Frame
	messages chan []byte
	done     chan struct{}
	once     sync.Once
	err      error
}

func Dial(ctx context.Context, o TransportOptions, bot, secret string, fence FenceWrite) (*Connection, error) {
	if o.URL == "" {
		o.URL = DefaultWebSocketURL
	}
	u, e := url.Parse(o.URL)
	if e != nil || u.User != nil || u.Fragment != "" || u.RawQuery != "" {
		return nil, ErrInvalid
	}
	ip := net.ParseIP(u.Hostname())
	local := o.AllowLoopback && (u.Hostname() == "localhost" || ip != nil && ip.IsLoopback())
	if u.Scheme != "wss" && !(u.Scheme == "ws" && local) {
		return nil, ErrInvalid
	}
	if o.AckTimeout <= 0 {
		o.AckTimeout = 10 * time.Second
	}
	if o.Heartbeat <= 0 {
		o.Heartbeat = 30 * time.Second
	}
	if o.WriteTimeout <= 0 {
		o.WriteTimeout = 5 * time.Second
	}
	d := websocket.Dialer{HandshakeTimeout: 10 * time.Second, Proxy: nil, EnableCompression: false}
	ws, resp, e := d.DialContext(ctx, o.URL, nil)
	if resp != nil && resp.Body != nil {
		resp.Body.Close()
	}
	if e != nil {
		return nil, ErrUnavailable
	}
	c := &Connection{socket: ws, options: o, fence: fence, pending: map[string]chan Frame{}, messages: make(chan []byte, 64), done: make(chan struct{})}
	ws.SetReadLimit(1 << 20)
	go c.read()
	body, _ := json.Marshal(map[string]string{"bot_id": bot, "secret": secret})
	e = c.request(ctx, Frame{Command: "aibot_subscribe", Headers: Headers{"aibot_subscribe_" + platform.ID()}, Body: body})
	if e != nil {
		c.Close()
		return nil, e
	}
	go c.heartbeat()
	return c, nil
}
func (c *Connection) Done() <-chan struct{}   { return c.done }
func (c *Connection) Messages() <-chan []byte { return c.messages }
func (c *Connection) Err() error              { c.mu.Lock(); defer c.mu.Unlock(); return c.err }
func (c *Connection) stop(e error) {
	c.once.Do(func() { c.mu.Lock(); c.err = e; c.mu.Unlock(); close(c.done); c.socket.Close() })
}
func (c *Connection) Close() { c.stop(ErrUnavailable) }
func (c *Connection) read() {
	for {
		kind, raw, e := c.socket.ReadMessage()
		if e != nil {
			c.stop(ErrUnavailable)
			return
		}
		if kind != websocket.TextMessage {
			c.stop(ErrInvalid)
			return
		}
		var f Frame
		if strictJSON(raw, &f) != nil || f.Headers.RequestID == "" {
			c.stop(ErrInvalid)
			return
		}
		switch f.Command {
		case "aibot_event_callback":
			var v struct {
				Event struct {
					Type string `json:"eventtype"`
				} `json:"event"`
			}
			if json.Unmarshal(f.Body, &v) == nil && v.Event.Type == "disconnected_event" {
				c.stop(ErrDisplaced)
				return
			}
		case "aibot_msg_callback":
			select {
			case c.messages <- raw:
			case <-c.done:
				return
			default:
				c.stop(ErrUnavailable)
				return
			}
		case "":
			c.mu.Lock()
			ch := c.pending[f.Headers.RequestID]
			c.mu.Unlock()
			if ch != nil {
				select {
				case ch <- f:
				default:
				}
			}
		}
	}
}
func (c *Connection) write(ctx context.Context, f Frame) error {
	fn := func() error {
		c.writes.Lock()
		defer c.writes.Unlock()
		select {
		case <-c.done:
			return ErrUnavailable
		default:
		}
		if e := c.socket.SetWriteDeadline(time.Now().Add(c.options.WriteTimeout)); e != nil {
			return ErrUnavailable
		}
		return c.socket.WriteJSON(f)
	}
	if c.fence != nil {
		return c.fence(ctx, fn)
	}
	return fn()
}

// Global serialization also prevents same-req-id stream ACK ambiguity. On timeout
// the socket is closed so a late ACK cannot acknowledge any later cumulative frame.
func (c *Connection) request(ctx context.Context, f Frame) error {
	c.serial.Lock()
	defer c.serial.Unlock()
	ch := make(chan Frame, 1)
	c.mu.Lock()
	c.pending[f.Headers.RequestID] = ch
	c.mu.Unlock()
	defer func() { c.mu.Lock(); delete(c.pending, f.Headers.RequestID); c.mu.Unlock() }()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-c.done:
		return c.Err()
	default:
	}
	if e := c.write(ctx, f); e != nil {
		c.stop(e)
		if e == ErrLeaseLost {
			return e
		}
		return ErrDeliveryUnknown
	}
	timer := time.NewTimer(c.options.AckTimeout)
	defer timer.Stop()
	select {
	case ack := <-ch:
		if ack.ErrorCode == nil {
			c.stop(ErrInvalid)
			return ErrDeliveryUnknown
		}
		if *ack.ErrorCode != 0 {
			return ErrRejected
		}
		return nil
	case <-ctx.Done():
		c.stop(ErrDeliveryUnknown)
		return ErrDeliveryUnknown
	case <-timer.C:
		c.stop(ErrDeliveryUnknown)
		return ErrDeliveryUnknown
	case <-c.done:
		return ErrDeliveryUnknown
	}
}
func (c *Connection) Reply(ctx context.Context, f Frame) error {
	if f.Command != "aibot_respond_msg" {
		return ErrInvalid
	}
	return c.request(ctx, f)
}
func (c *Connection) heartbeat() {
	ticker := time.NewTicker(c.options.Heartbeat)
	defer ticker.Stop()
	for {
		select {
		case <-c.done:
			return
		case <-ticker.C:
			ctx, cancel := context.WithTimeout(context.Background(), c.options.AckTimeout+c.options.WriteTimeout)
			e := c.request(ctx, Frame{Command: "ping", Headers: Headers{"ping_" + platform.ID()}})
			cancel()
			if e != nil {
				c.stop(e)
				return
			}
		}
	}
}
