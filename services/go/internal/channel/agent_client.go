package channel

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

// HTTPAgentClient never accepts identity or scope supplied by a chat message.
// Auth resolves the opaque, persisted message context and Agent owns run deduplication.
type HTTPAgentClient struct {
	Client                    *platform.Client
	AuthURL, AgentURL, WebURL string
}

type agentClaim struct {
	Text        string   `json:"text"`
	CitationIDs []string `json:"citation_ids"`
}
type agentAnswer struct {
	Facts      []agentClaim `json:"facts"`
	Inferences []agentClaim `json:"inferences"`
	Gaps       []string     `json:"gaps"`
}
type agentCitation struct {
	ID         string          `json:"id"`
	SpaceID    string          `json:"space_id"`
	PageID     string          `json:"page_id"`
	RevisionID string          `json:"revision_id"`
	Evidence   json.RawMessage `json:"evidence"`
}
type channelRunView struct {
	ID              string          `json:"id"`
	SessionID       string          `json:"session_id"`
	Entrypoint      string          `json:"entrypoint"`
	AgentID         string          `json:"agent_id"`
	ConfigurationID string          `json:"configuration_id"`
	ActualScope     []string        `json:"actual_scope"`
	Status          string          `json:"status"`
	EventSeq        *int64          `json:"event_seq"`
	ContentHidden   *bool           `json:"content_hidden"`
	Answer          *agentAnswer    `json:"answer"`
	AnswerComplete  *bool           `json:"answer_complete"`
	Citations       []agentCitation `json:"citations"`
}

func (c *HTTPAgentClient) token(ctx context.Context, v RunContext) (string, error) {
	if c.Client == nil || c.Client.HTTP == nil || c.Client.Security == nil || !key(v.ID) {
		return "", ErrUnavailable
	}
	var out struct {
		AccessToken string `json:"access_token"`
		TokenType   string `json:"token_type"`
		ExpiresIn   int    `json:"expires_in"`
		Scope       string `json:"scope"`
	}
	if e := c.Client.Call(ctx, "auth", c.AuthURL, "POST", "/internal/v1/channel-token", "", map[string]string{"context_id": v.ID}, &out); e != nil {
		return "", e
	}
	if !strings.HasPrefix(out.AccessToken, "skc_") || len(out.AccessToken) <= 4 || len(out.AccessToken) > 16384 || strings.ContainsAny(out.AccessToken, " \t\r\n") || out.TokenType != "Bearer" || out.Scope != "knowledge:read" || out.ExpiresIn < 1 || out.ExpiresIn > 180 {
		return "", ErrUnavailable
	}
	return "Bearer " + out.AccessToken, nil
}

func (v channelRunView) terminal() bool {
	switch v.Status {
	case "completed", "partial", "failed", "cancelled":
		return true
	}
	return false
}

func (v channelRunView) validate(scope RunContext, original *channelRunView) error {
	if v.ContentHidden == nil || *v.ContentHidden {
		return ErrDenied
	}
	if !key(v.ID) || !key(v.SessionID) || v.Entrypoint != "channel" || v.AgentID != scope.AgentID || v.ConfigurationID != scope.AgentConfigurationID || !key(v.AgentID) || !key(v.ConfigurationID) || v.EventSeq == nil || *v.EventSeq < 0 || v.AnswerComplete == nil {
		return ErrUnavailable
	}
	if !v.terminal() && v.Status != "queued" && v.Status != "running" {
		return ErrUnavailable
	}
	if *v.AnswerComplete != (v.Status == "completed" && v.Answer != nil) || (v.Status == "completed" && v.Answer == nil) {
		return ErrUnavailable
	}
	allowed := map[string]bool{}
	for _, id := range scope.SpaceIDs {
		allowed[id] = true
	}
	seen := map[string]bool{}
	if len(v.ActualScope) == 0 || len(v.ActualScope) > 100 {
		return ErrDenied
	}
	for _, id := range v.ActualScope {
		if !key(id) || !allowed[id] || seen[id] {
			return ErrDenied
		}
		seen[id] = true
	}
	if original != nil {
		if v.ID != original.ID || v.SessionID != original.SessionID || len(v.ActualScope) != len(original.ActualScope) {
			return ErrDenied
		}
		for _, id := range original.ActualScope {
			if !seen[id] {
				return ErrDenied
			}
		}
	}
	citations := map[string]bool{}
	if len(v.Citations) > 1000 {
		return ErrUnavailable
	}
	for _, citation := range v.Citations {
		if !key(citation.ID) || !key(citation.PageID) || !key(citation.RevisionID) || !seen[citation.SpaceID] || citations[citation.ID] || len(citation.Evidence) == 0 || citation.Evidence[0] != '{' {
			return ErrUnavailable
		}
		citations[citation.ID] = true
	}
	if v.Answer != nil {
		if len(v.Answer.Facts) > 24 || len(v.Answer.Inferences) > 24 || len(v.Answer.Gaps) > 20 {
			return ErrUnavailable
		}
		for _, claims := range [][]agentClaim{v.Answer.Facts, v.Answer.Inferences} {
			for _, claim := range claims {
				if !answerText(claim.Text, 8000) || len(claim.CitationIDs) == 0 || len(claim.CitationIDs) > 50 {
					return ErrUnavailable
				}
				refs := map[string]bool{}
				for _, id := range claim.CitationIDs {
					if !citations[id] || refs[id] {
						return ErrUnavailable
					}
					refs[id] = true
				}
			}
		}
		for _, gap := range v.Answer.Gaps {
			if !answerText(gap, 2000) {
				return ErrUnavailable
			}
		}
	}
	return nil
}

func answerText(s string, max int) bool {
	return strings.TrimSpace(s) != "" && utf8.ValidString(s) && utf8.RuneCountInString(s) <= max && !strings.ContainsRune(s, '\x00')
}

func (c *HTTPAgentClient) read(ctx context.Context, bearer string, scope RunContext, original channelRunView) (channelRunView, error) {
	var next channelRunView
	e := c.Client.Call(ctx, "agent", c.AgentURL, "GET", "/internal/v1/channel/runs/"+original.ID, bearer, nil, &next)
	if e == nil {
		e = next.validate(scope, &original)
	}
	return next, e
}

// Every captured claim must still be in the newly authorized result. New blocks
// may be grouped into an earlier section, so byte-prefix comparison is incorrect.
func preservesAnswer(old, current channelRunView) bool {
	// Stop/failure can preserve earlier evidence. Its presence is not permission
	// to send a queued "still running" frame after that operation has ended.
	if !old.terminal() && current.terminal() && current.Status != "completed" {
		return false
	}
	if old.terminal() && (current.Status != old.Status || *current.AnswerComplete != *old.AnswerComplete) {
		return false
	}
	if old.Answer == nil {
		return true
	}
	if current.Answer == nil {
		return false
	}
	subset := func(before, after []string) bool {
		counts := map[string]int{}
		for _, s := range after {
			counts[s]++
		}
		for _, s := range before {
			counts[s]--
			if counts[s] < 0 {
				return false
			}
		}
		return true
	}
	claims := func(v []agentClaim) []string {
		result := []string{}
		for _, claim := range v {
			result = append(result, digest(claim))
		}
		return result
	}
	if !subset(claims(old.Answer.Facts), claims(current.Answer.Facts)) || !subset(claims(old.Answer.Inferences), claims(current.Answer.Inferences)) || !subset(old.Answer.Gaps, current.Answer.Gaps) {
		return false
	}
	currentCitations := map[string]string{}
	for _, ref := range current.Citations {
		currentCitations[ref.ID] = digest(ref)
	}
	for _, ref := range old.Citations {
		if currentCitations[ref.ID] != digest(ref) {
			return false
		}
	}
	return true
}

var escapeAnswerMarkdown = strings.NewReplacer("\\", "\\\\", "[", "\\[", "]", "\\]", "(", "\\(", ")", "\\)", "<", "&lt;", ">", "&gt;", "`", "\\`", "*", "\\*", "_", "\\_", "#", "\\#", "!", "\\!")

func (c *HTTPAgentClient) update(bearer string, scope RunContext, original, view channelRunView) Update {
	link := ""
	if validWebURL(c.WebURL) {
		link = strings.TrimRight(c.WebURL, "/") + "/runs/" + view.ID
	}
	var out strings.Builder
	if view.Answer != nil {
		sections := []struct {
			label  string
			claims []agentClaim
		}{{"事实", view.Answer.Facts}, {"推断", view.Answer.Inferences}}
		for _, section := range sections {
			if len(section.claims) > 0 {
				out.WriteString("**" + section.label + "**\n")
				for _, claim := range section.claims {
					out.WriteString("- " + escapeAnswerMarkdown.Replace(claim.Text))
					if link != "" {
						for i, id := range claim.CitationIDs {
							out.WriteString(" [证据" + strconv.Itoa(i+1) + "](" + link + "?citation=" + id + ")")
						}
					}
					out.WriteByte('\n')
				}
				out.WriteByte('\n')
			}
		}
		if len(view.Answer.Gaps) > 0 {
			out.WriteString("**证据缺口**\n")
			for _, gap := range view.Answer.Gaps {
				out.WriteString("- " + escapeAnswerMarkdown.Replace(gap) + "\n")
			}
		}
	}
	if out.Len() == 0 {
		out.WriteString("已接收问题，正在检索授权范围内的知识。\n")
	}
	if view.terminal() && !*view.AnswerComplete {
		out.WriteString("\n运行已结束，结果尚不完整。请查看平台中的运行状态与证据缺口。\n")
	}
	if view.terminal() && link != "" {
		out.WriteString("\n[查看完整结果（需登录）](" + link + ")")
	}
	return Update{RunID: view.ID, Content: out.String(), Finished: view.terminal(), URL: link, BeforeSend: func(ctx context.Context) error {
		current, e := c.read(ctx, bearer, scope, original)
		if e != nil {
			return e
		}
		if !preservesAnswer(view, current) {
			return ErrDenied
		}
		return nil
	}}
}

func (c *HTTPAgentClient) Execute(ctx context.Context, scope RunContext, question, _ string, emit func(Update) error) (string, error) {
	if emit == nil || !answerText(question, 8192) {
		return "", ErrInvalid
	}
	bearer, e := c.token(ctx, scope)
	if e != nil {
		return "", e
	}
	var original channelRunView
	// Never retry an uncertain create. A redelivered platform message is deduplicated
	// by Agent's immutable channel/message record, not a new client-supplied key.
	if e = c.Client.Call(ctx, "agent", c.AgentURL, "POST", "/internal/v1/channel/runs", bearer, map[string]string{"question": question}, &original); e != nil {
		return "", e
	}
	if e = original.validate(scope, nil); e != nil {
		return "", e
	}
	last := c.update(bearer, scope, original, original)
	if e = emit(last); e != nil || last.Finished {
		return original.ID, e
	}
	cursor := int64(0)
	refresh := func() (bool, error) {
		view, e := c.read(ctx, bearer, scope, original)
		if e != nil {
			return false, e
		}
		next := c.update(bearer, scope, original, view)
		if next.Content != last.Content || next.Finished != last.Finished {
			if e = emit(next); e != nil {
				return false, e
			}
			last = next
		}
		return next.Finished, nil
	}
	for attempt := 0; attempt < 4; attempt++ {
		done, e := c.stream(ctx, bearer, original.ID, &cursor, refresh)
		if done || e != nil && !errors.Is(e, io.ErrUnexpectedEOF) {
			return original.ID, e
		}
		if done, e = refresh(); done || e != nil {
			return original.ID, e
		}
		if attempt < 3 {
			timer := time.NewTimer(time.Duration(100*(1<<attempt)) * time.Millisecond)
			select {
			case <-ctx.Done():
				timer.Stop()
				return original.ID, ctx.Err()
			case <-timer.C:
			}
		}
	}
	return original.ID, ErrUnavailable
}

// Events are cursors and invalidation signals only. Their payload never becomes
// an answer; GET rechecks all current input and audience permissions each time.
func (c *HTTPAgentClient) stream(ctx context.Context, bearer, run string, cursor *int64, refresh func() (bool, error)) (bool, error) {
	req, e := http.NewRequestWithContext(ctx, "GET", strings.TrimRight(c.AgentURL, "/")+"/internal/v1/channel/runs/"+run+"/events", nil)
	if e != nil {
		return false, ErrUnavailable
	}
	token, e := c.Client.Security.Mint(c.Client.Service, "agent")
	if e != nil {
		return false, ErrUnavailable
	}
	req.Header.Set("X-Service-Token", token)
	req.Header.Set("Authorization", bearer)
	req.Header.Set("X-Request-ID", platform.RequestID(ctx))
	req.Header.Set("Accept", "text/event-stream")
	if *cursor > 0 {
		req.Header.Set("Last-Event-ID", strconv.FormatInt(*cursor, 10))
	}
	client := *c.Client.HTTP
	client.Timeout = 0 // The worker's run context bounds the long-lived response.
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	resp, e := client.Do(req)
	if e != nil {
		if ctx.Err() != nil {
			return false, ctx.Err()
		}
		return false, io.ErrUnexpectedEOF
	}
	defer resp.Body.Close()
	mediaType, _, _ := mime.ParseMediaType(resp.Header.Get("Content-Type"))
	if resp.StatusCode != 200 || mediaType != "text/event-stream" {
		return false, ErrUnavailable
	}
	scanner := bufio.NewScanner(io.LimitReader(resp.Body, 16<<20))
	scanner.Buffer(make([]byte, 4096), 1<<20)
	var id, kind string
	var data strings.Builder
	frames := 0
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			if id == "" && kind == "" && data.Len() == 0 {
				continue
			}
			frames++
			seq, err := strconv.ParseInt(id, 10, 64)
			var event struct {
				Seq  int64  `json:"seq"`
				Type string `json:"type"`
			}
			if frames > 5000 || err != nil || seq <= 0 || kind == "" || json.Unmarshal([]byte(data.String()), &event) != nil || event.Seq != seq || event.Type != kind {
				return false, ErrUnavailable
			}
			if seq > *cursor {
				done, err := refresh()
				if err != nil || done {
					return done, err
				}
				*cursor = seq
			}
			id, kind = "", ""
			data.Reset()
			continue
		}
		if strings.HasPrefix(line, ":") {
			continue
		}
		field, value, _ := strings.Cut(line, ":")
		value = strings.TrimPrefix(value, " ")
		switch field {
		case "id":
			if id != "" {
				return false, ErrUnavailable
			}
			id = value
		case "event":
			if kind != "" {
				return false, ErrUnavailable
			}
			kind = value
		case "data":
			data.WriteString(value)
			data.WriteByte('\n')
			if data.Len() > 1<<20 {
				return false, ErrUnavailable
			}
		}
	}
	if ctx.Err() != nil {
		return false, ctx.Err()
	}
	if scanner.Err() != nil && !errors.Is(scanner.Err(), io.ErrUnexpectedEOF) {
		return false, ErrUnavailable
	}
	return false, io.ErrUnexpectedEOF
}

func (c *HTTPAgentClient) Cancel(ctx context.Context, scope RunContext) error {
	bearer, e := c.token(ctx, scope)
	if e != nil {
		return e
	}
	var result struct {
		Cancelled *bool  `json:"cancelled"`
		RunID     string `json:"run_id"`
	}
	if e = c.Client.Call(ctx, "agent", c.AgentURL, "POST", "/internal/v1/channel/conversation/cancel", bearer, struct{}{}, &result); e != nil {
		return e
	}
	if result.Cancelled == nil || *result.Cancelled && !key(result.RunID) {
		return ErrUnavailable
	}
	return nil
}

func (c *HTTPAgentClient) Clear(ctx context.Context, scope RunContext) error {
	bearer, e := c.token(ctx, scope)
	if e != nil {
		return e
	}
	var result struct {
		Cleared *bool `json:"cleared"`
	}
	if e = c.Client.Call(ctx, "agent", c.AgentURL, "POST", "/internal/v1/channel/conversation/clear", bearer, struct{}{}, &result); e != nil {
		return e
	}
	if result.Cleared == nil || !*result.Cleared {
		return ErrUnavailable
	}
	return nil
}
