package channel

import (
	"encoding/base64"
	"strings"
)

// SecretBoxFromBase64 accepts init.py's URL-safe format and standard base64,
// preserving the actual encryption key and all existing encrypted Bot credentials.
func SecretBoxFromBase64(encoded string) (*SecretBox, error) {
	if strings.ContainsAny(encoded, " \t\r\n") {
		return nil, ErrInvalid
	}
	key, err := base64.StdEncoding.Strict().DecodeString(encoded)
	if err != nil {
		key, err = base64.URLEncoding.Strict().DecodeString(encoded)
	}
	if err != nil {
		return nil, ErrInvalid
	}
	return NewSecretBox(key)
}
