package channel

import (
	"bytes"
	"encoding/json"
	"io"
	"strings"
	"unicode/utf8"
)

type Headers struct {
	RequestID string `json:"req_id"`
}
type Frame struct {
	Command   string          `json:"cmd,omitempty"`
	Headers   Headers         `json:"headers"`
	Body      json.RawMessage `json:"body,omitempty"`
	ErrorCode *int            `json:"errcode,omitempty"`
}
type Message struct {
	ID        string
	RequestID string
	UserID    string
	ChatID    string
	ChatType  string
	Text      string
}

func ParseMessage(raw []byte, bot string) (Message, error) {
	var f Frame
	if len(raw) > 1<<20 || !utf8.Valid(raw) || strictJSON(raw, &f) != nil || f.Command != "aibot_msg_callback" || !key(f.Headers.RequestID) {
		return Message{}, ErrInvalid
	}
	var b struct {
		ID       string `json:"msgid"`
		Bot      string `json:"aibotid"`
		ChatID   string `json:"chatid"`
		ChatType string `json:"chattype"`
		From     struct {
			ID string `json:"userid"`
		} `json:"from"`
		Type string `json:"msgtype"`
		Text struct {
			Content string `json:"content"`
		} `json:"text"`
	}
	if strictJSON(f.Body, &b) != nil || !key(b.ID) || b.Bot != bot || !key(b.From.ID) || b.Type != "text" || len(b.Text.Content) > 32768 || strings.TrimSpace(b.Text.Content) == "" || (b.ChatType != "single" && b.ChatType != "group") || (b.ChatType == "group" && !key(b.ChatID)) {
		return Message{}, ErrInvalid
	}
	return Message{b.ID, f.Headers.RequestID, b.From.ID, b.ChatID, b.ChatType, b.Text.Content}, nil
}

// Identity-bearing duplicate keys are rejected, unknown upstream fields remain compatible.
func strictJSON(raw []byte, out any) error {
	d := json.NewDecoder(bytes.NewReader(raw))
	if e := jsonValue(d, 0); e != nil {
		return ErrInvalid
	}
	if _, e := d.Token(); e != io.EOF {
		return ErrInvalid
	}
	return json.Unmarshal(raw, out)
}
func jsonValue(d *json.Decoder, depth int) error {
	if depth > 64 {
		return ErrInvalid
	}
	t, e := d.Token()
	if e != nil {
		return e
	}
	delim, ok := t.(json.Delim)
	if !ok {
		return nil
	}
	if delim == '{' {
		seen := map[string]bool{}
		for d.More() {
			k, e := d.Token()
			if e != nil {
				return e
			}
			s, ok := k.(string)
			if !ok || seen[s] {
				return ErrInvalid
			}
			seen[s] = true
			if e = jsonValue(d, depth+1); e != nil {
				return e
			}
		}
	} else if delim == '[' {
		for d.More() {
			if e = jsonValue(d, depth+1); e != nil {
				return e
			}
		}
	} else {
		return ErrInvalid
	}
	_, e = d.Token()
	return e
}
func responseFrame(req, stream, content string, finish bool) Frame {
	b, _ := json.Marshal(map[string]any{"msgtype": "stream", "stream": map[string]any{"id": stream, "content": content, "finish": finish}})
	return Frame{Command: "aibot_respond_msg", Headers: Headers{req}, Body: b}
}

// The upstream group callback is already directed to this bot; only a leading
// display-name address is omitted when recognizing the three control commands.
// Evidence questions retain their original text.
func MessageCommand(m Message) string {
	text := strings.TrimSpace(m.Text)
	if m.ChatType == "group" && strings.HasPrefix(text, "@") {
		if i := strings.IndexFunc(text, func(r rune) bool { return r == ' ' || r == '\t' || r == '\n' || r == '\u2005' || r == '\u00a0' }); i >= 0 {
			return strings.TrimSpace(text[i:])
		}
	}
	return text
}
