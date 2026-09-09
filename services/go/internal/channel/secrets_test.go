package channel

import (
	"bytes"
	"encoding/base64"
	"testing"
)

func TestSecretBoxAcceptsInitializerURLSafeAndStandardBase64(t *testing.T) {
	// init.py produces padded URL-safe base64. Both alphabets must decode to
	// the identical key; restarting with an existing key must not rotate it.
	key := bytes.Repeat([]byte{251}, 32)
	standard, e := SecretBoxFromBase64(base64.StdEncoding.EncodeToString(key))
	if e != nil { t.Fatal(e) }
	sealed, e := standard.Seal("bot", "credential")
	if e != nil { t.Fatal(e) }
	urlSafe, e := SecretBoxFromBase64(base64.URLEncoding.EncodeToString(key))
	if e != nil { t.Fatal("initializer key rejected") }
	value, e := urlSafe.Open("bot", sealed)
	if e != nil || value != "credential" { t.Fatal("existing credential no longer decrypts") }
	for _, bad := range []string{"", "not base64", base64.StdEncoding.EncodeToString(key[:31]), base64.URLEncoding.EncodeToString(key)+"\\n		if _, e := SecretBoxFromBase64(bad); e == nil { t.Fatal("invalid deployment key accepted") }
	}
}
