package channel

import (
	"bytes"
	"context"
	"encoding/json"
	"strings"
	"testing"
	"unicode/utf8"
)

func TestSecretBoundToChannelAndNeverSerialized(t *testing.T) {
	box, err := NewSecretBox(bytes.Repeat([]byte{7}, 32))
	if err != nil {
		t.Fatal(err)
	}
	cipher, err := box.Seal("channel-1", "secret-bot")
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(cipher, []byte("secret-bot")) {
		t.Fatal("plaintext stored")
	}
	if _, err = box.Open("channel-2", cipher); err == nil {
		t.Fatal("ciphertext transplant accepted")
	}
	clear, err := box.Open("channel-1", cipher)
	if err != nil || clear != "secret-bot" {
		t.Fatal("decrypt failed")
	}
	value, _ := json.Marshal(Config{ID: "channel-1", SecretCipher: cipher})
	if bytes.Contains(value, cipher) {
		t.Fatal("secret serialized")
	}
}

func TestBoundedUTF8Reply(t *testing.T) {
	long := strings.Repeat("研发😀", 5000)
	got := BoundedReply(long, "https://knowledge.example/runs/123")
	if len(got) > 20480 || !utf8.ValidString(got) || !strings.Contains(got, "https://knowledge.example/runs/123") {
		t.Fatal("invalid bounded reply")
	}
	if BoundedReply("完整回答", "") != "完整回答" {
		t.Fatal("short response changed")
	}
}

func TestProtocolRejectsMalformedAndNonText(t *testing.T) {
	for _, raw := range []string{`null`, `[]`, `{"cmd":"aibot_msg_callback","headers":{"req_id":"r"},"body":{}}`, `{"cmd":"aibot_msg_callback","headers":{"req_id":"r"},"body":{"msgid":"m","aibotid":"bot","chattype":"single","from":{"userid":"u"},"msgtype":"image"}}`} {
		if _, err := ParseMessage([]byte(raw), "bot"); err == nil {
			t.Fatalf("accepted invalid message %s", raw)
		}
	}
	raw := []byte(`{"cmd":"aibot_msg_callback","headers":{"req_id":"r"},"body":{"msgid":"m","aibotid":"bot","chattype":"single","from":{"userid":"u"},"msgtype":"text","text":{"content":"问题"}}}`)
	m, err := ParseMessage(raw, "bot")
	if err != nil || m.UserID != "u" || m.Text != "问题" {
		t.Fatal("valid message rejected", err)
	}
	if _, err = ParseMessage(raw, "other"); err == nil {
		t.Fatal("wrong bot accepted")
	}
	if ConversationKey("c", "u", "single", "", 1, 1, 0, 1) == ConversationKey("c", "u", "group", "g", 1, 1, 1, 1) {
		t.Fatal("private/group share memory")
	}
}

func TestUnconfiguredAgentFailsClosed(t *testing.T) {
	if _, err := (UnavailableAgent{}).Execute(context.Background(), RunContext{}, "question", "key", func(Update) error { return nil }); err == nil {
		t.Fatal("unconfigured agent accepted")
	}
}
