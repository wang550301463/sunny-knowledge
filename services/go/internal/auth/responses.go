package auth

// DelegatedTokenResponse is shared by message and exact-run read brokers.
// Its fields describe the existing wire response without changing token policy.
type DelegatedTokenResponse struct {
	AccessToken string `json:"access_token"`
	TokenType   string `json:"token_type"`
	ExpiresIn   int64  `json:"expires_in"`
	Scope       string `json:"scope"`
}
