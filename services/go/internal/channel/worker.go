package channel

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"strings"
	"sync"
	"time"
)

type WorkerOptions struct {
	Transport     TransportOptions
	LeaseTTL      time.Duration
	PollInterval  time.Duration
	WebURL        string
	MaxConcurrent int
}
type Worker struct {
	Store   *Store
	Agent   AgentClient
	Options WorkerOptions
	owner   string
}

func NewWorker(s *Store, a AgentClient, o WorkerOptions) *Worker {
	if a == nil {
		a = UnavailableAgent{}
	}
	if o.LeaseTTL <= 0 {
		o.LeaseTTL = 30 * time.Second
	}
	if o.PollInterval <= 0 {
		o.PollInterval = 2 * time.Second
	}
	if o.MaxConcurrent <= 0 {
		o.MaxConcurrent = 10
	}
	return &Worker{s, a, o, platform.ID()}
}
func (w *Worker) Run(ctx context.Context) error {
	type active struct {
		version int64
		cancel  context.CancelFunc
		done    chan struct{}
	}
	running := map[string]active{}
	var wg sync.WaitGroup
	defer func() {
		for _, v := range running {
			v.cancel()
		}
		wg.Wait()
	}()
	ticker := time.NewTicker(w.Options.PollInterval)
	defer ticker.Stop()
	for {
		configs, e := w.Store.Runnable(ctx)
		if e != nil {
			if ctx.Err() != nil {
				return nil
			}
			return ErrUnavailable
		}
		seen := map[string]bool{}
		for _, c := range configs {
			seen[c.ID] = true
			old, ok := running[c.ID]
			if ok {
				select {
				case <-old.done:
					delete(running, c.ID)
					ok = false
				default:
				}
				if ok && old.version != c.Version {
					old.cancel()
					continue
				}
			}
			if !ok {
				task, cancel := context.WithCancel(ctx)
				done := make(chan struct{})
				running[c.ID] = active{c.Version, cancel, done}
				wg.Add(1)
				go func(c Config) { defer wg.Done(); defer close(done); w.serve(task, c) }(c)
			}
		}
		for id, v := range running {
			if !seen[id] {
				v.cancel()
			}
		}
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
	}
}
func (w *Worker) serve(ctx context.Context, c Config) {
	delay := time.Second
	for attempts := 0; attempts < 10; attempts++ {
		if ctx.Err() != nil {
			return
		}
		l, e := w.Store.Acquire(ctx, c.ID, w.owner+":"+platform.ID(), w.Options.LeaseTTL)
		if e != nil {
			return
		}
		e = w.connection(ctx, c, l)
		cleanup, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		w.Store.Release(cleanup, l)
		cancel()
		if !c.Enabled || errors.Is(e, ErrDisplaced) || errors.Is(e, ErrRejected) || errors.Is(e, ErrLeaseLost) || ctx.Err() != nil {
			return
		}
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
		if delay < 30*time.Second {
			delay *= 2
			if delay > 30*time.Second {
				delay = 30 * time.Second
			}
		}
	}
}
func (w *Worker) connection(parent context.Context, c Config, l Lease) error {
	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	secret, e := w.Store.Box.Open(c.ID, c.SecretCipher)
	if e != nil {
		return e
	}
	if e = w.Store.Recover(ctx, l); e != nil {
		return e
	}
	conn, e := Dial(ctx, w.Options.Transport, c.BotID, secret, func(ctx context.Context, fn func() error) error { return w.Store.WithFence(ctx, l, fn) })
	if e != nil {
		status := "connection_failed"
		if errors.Is(e, ErrRejected) {
			status = "auth_failed"
		}
		w.Store.Status(ctx, l, c.Version, status, false)
		return e
	}
	defer conn.Close()
	if e = w.Store.Status(ctx, l, c.Version, "connected", true); e != nil {
		return e
	}
	if !c.Enabled {
		return nil
	}
	var jobs sync.WaitGroup
	defer func() { cancel(); conn.Close(); jobs.Wait() }()
	ticker := time.NewTicker(w.Options.LeaseTTL / 3)
	defer ticker.Stop()
	sem := make(chan struct{}, w.Options.MaxConcurrent)
	control := make(chan struct{}, 2)
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			if e = w.Store.Renew(ctx, l, w.Options.LeaseTTL); e != nil {
				return e
			}
		case <-conn.Done():
			if errors.Is(conn.Err(), ErrDisplaced) {
				w.Store.Status(ctx, l, c.Version, "displaced", false)
			} else {
				w.Store.Status(ctx, l, c.Version, "reconnecting", false)
			}
			return conn.Err()
		case raw := <-conn.Messages():
			m, e := ParseMessage(raw, c.BotID)
			if e != nil {
				continue
			}
			fresh, e := w.Store.Accept(ctx, l, m)
			if e != nil {
				return e
			}
			if !fresh {
				continue
			}
			capacity := sem
			switch MessageCommand(m) {
			case "/停止", "停止", "/stop", "/清空", "清空", "/clear":
				capacity = control
			}
			select {
			case capacity <- struct{}{}:
				jobs.Add(1)
				go func() { defer jobs.Done(); defer func() { <-capacity }(); w.handle(ctx, c, l, conn, m) }()
			default:
				w.Store.FinishMessage(ctx, l, m.ID, "busy", "")
			}
		}
	}
}
func (w *Worker) handle(parent context.Context, c Config, l Lease, conn *Connection, m Message) {
	ctx, cancel := context.WithTimeout(parent, 180*time.Second)
	defer cancel()
	seq := int64(0)
	stream := platform.ID()
	finished := false
	runID := ""
	state := "failed"
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		w.Store.FinishMessage(cleanup, l, m.ID, state, runID)
	}()
	emit := func(v Update) error {
		if finished {
			return ErrConflict
		}
		seq++
		link := ""
		if key(v.RunID) && validWebURL(w.Options.WebURL) {
			link = strings.TrimRight(w.Options.WebURL, "/") + "/runs/" + v.RunID
		}
		content := BoundedReply(v.Content, link)
		id, e := w.Store.BeginDelivery(ctx, l, m.ID, seq, content, v.Finished)
		if e != nil {
			return e
		}
		e = conn.Reply(ctx, responseFrame(m.RequestID, stream, content, v.Finished))
		delivery := "sent"
		if e != nil {
			delivery = "delivery_unknown"
			state = "delivery_unknown"
			var unsent *UnsentError
			if errors.As(e, &unsent) {
				delivery = "not_sent"
				state = "failed"
			} else if errors.Is(e, ErrRejected) {
				delivery = "rejected"
				state = "failed"
			}
		}
		cleanup, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		save := w.Store.EndDelivery(cleanup, id, delivery)
		cancel()
		if save != nil {
			conn.Close()
			state = "delivery_unknown"
			return ErrDeliveryUnknown
		}
		if e == nil && v.Finished {
			finished = true
			state = "sent"
		}
		return e
	}
	final := func(text string) { emit(Update{Content: text, Finished: true}) }
	command := strings.TrimSpace(m.Text)
	if command == "/帮助" || command == "帮助" || command == "/help" {
		final("发送文字问题开始问答。命令：/绑定、/停止、/清空。群内请 @ 机器人；群知识范围由管理员授权。")
		return
	}
	if m.ChatType == "single" && (command == "/绑定" || command == "绑定" || command == "/bind") {
		w.bindReply(ctx, l, m, final)
		return
	}
	if m.ChatType == "single" && strings.HasPrefix(command, "/确认 ") {
		parts := strings.Fields(command)
		if len(parts) != 3 || w.Store.ConfirmChallenge(ctx, l, m.UserID, parts[1], parts[2]) != nil {
			final("绑定确认无效或已过期，请重新 /绑定。")
		} else {
			final("账号绑定完成。现在可以发送知识问题。")
		}
		return
	}
	v, e := w.Store.CreateContext(ctx, l, m)
	if e != nil {
		if errors.Is(e, ErrDenied) {
			if m.ChatType == "single" {
				w.bindReply(ctx, l, m, final)
			} else {
				final("该群或账号尚未获得知识访问授权，请私聊机器人绑定账号并联系管理员。")
			}
		} else {
			final("身份授权服务暂不可用，请稍后重试。")
		}
		return
	}
	if command == "/停止" || command == "停止" || command == "/stop" {
		if w.Agent.Cancel(ctx, v) != nil {
			final("暂时无法确认停止状态，请在知识平台查看运行状态。")
		} else {
			final("已请求停止当前会话的运行。")
		}
		return
	}
	if command == "/清空" || command == "清空" || command == "/clear" {
		if w.Agent.Clear(ctx, v) != nil {
			final("暂时无法清空会话，请稍后重试。")
		} else if w.Store.ClearConversation(ctx, v) != nil {
			final("会话状态发生变化，请重试。")
		} else {
			final("当前会话已清空。")
		}
		return
	}
	runID, e = w.Agent.Execute(ctx, v, m.Text, digest([]string{c.ID, m.ID}), func(u Update) error {
		if _, e := w.Store.Introspect(ctx, v.ID); e != nil {
			return e
		}
		return emit(u)
	})
	if e != nil {
		if !finished && state != "delivery_unknown" {
			final("知识服务暂不可用或授权已变化，请在知识平台查看运行状态后重试。")
		}
		return
	}
	if !finished {
		final("运行未返回完整结果，请在知识平台查看运行状态。")
	}
}
func (w *Worker) bindReply(ctx context.Context, l Lease, m Message, final func(string)) {
	if !validWebURL(w.Options.WebURL) {
		final("请联系管理员配置知识平台访问地址后绑定账号。")
		return
	}
	ch, e := w.Store.BeginChallenge(ctx, l, m.UserID)
	if e != nil {
		final("暂时无法创建绑定请求，请稍后重试。")
		return
	}
	query, _ := json.Marshal(map[string]string{"challenge": ch.ID, "token": ch.WebToken})
	_ = query
	// Fragment keeps the one-time token out of reverse-proxy access logs and Referrer.
	final("请登录知识平台完成账号绑定，然后把网页显示的确认命令发回本次私聊：[绑定账号](" + strings.TrimRight(w.Options.WebURL, "/") + "/channels/bind#challenge=" + ch.ID + "&token=" + ch.WebToken + ")。链接 5 分钟有效。")
}
