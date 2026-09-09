package auth

import (
	"encoding/json"
	"testing"
)

func TestDelegatedTokenResponsePreservesNativeJSON(t *testing.T) {
	old, _ := json.Marshal(map[string]any{"access_token": "synthetic", "token_type": "Bearer", "expires_in": int64(179), "scope": "knowledge:read"})
	body, err := json.Marshal(DelegatedTokenResponse{AccessToken: "synthetic", TokenType: "Bearer", ExpiresIn: 179, Scope: "knowledge:read"})
	if err != nil {
		t.Fatal(err)
	}
	var expected, actual map[string]any
	if json.Unmarshal(old, &expected) != nil || json.Unmarshal(body, &actual) != nil {
		t.Fatal("JSON response failed")
	}
	if len(actual) != 4 {
		t.Fatal("unexpected response keys")
	}
	for key, value := range expected {
		if actual[key] != value {
			t.Fatalf("field %s changed", key)
		}
	}
}
