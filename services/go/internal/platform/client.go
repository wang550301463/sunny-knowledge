package platform

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/generatedcontracts"
	"io"
	"net/http"
	"strings"
	"time"
)

type Client struct {
	Service  string
	Security *ServiceSecurity
	HTTP     *http.Client
}

func NewClient(service string, security *ServiceSecurity) *Client {
	return &Client{Service: service, Security: security, HTTP: &http.Client{Transport: TraceTransport{}, Timeout: 10 * time.Second, CheckRedirect: func(req *http.Request, via []*http.Request) error { return http.ErrUseLastResponse }}}
}
func (c *Client) Call(ctx context.Context, target, base, method, path, bearer string, input, output any) error {
	var body io.Reader
	if input != nil {
		b, err := json.Marshal(input)
		if err != nil {
			return err
		}
		body = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(context.WithValue(ctx, targetKey{}, target), method, strings.TrimRight(base, "/")+path, body)
	if err != nil {
		return err
	}
	token, err := c.Security.Mint(c.Service, target)
	if err != nil {
		return err
	}
	req.Header.Set("X-Service-Token", token)
	if bearer != "" {
		req.Header.Set("Authorization", bearer)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Request-ID", RequestID(ctx))
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return fmt.Errorf("%s unavailable", target)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return &HTTPError{Status: resp.StatusCode}
	}
	if output != nil {
		return json.NewDecoder(io.LimitReader(resp.Body, 4<<20)).Decode(output)
	}
	return nil
}

type HTTPError struct{ Status int }

func (e *HTTPError) Error() string { return fmt.Sprintf("upstream request rejected (%d)", e.Status) }
func (c *Client) Resolve(ctx context.Context, authURL, bearer string) (Resolved, error) {
	var v Resolved
	operation := generatedcontracts.Operations["auth_post_internal_v1_resolve"]
	request, err := operation.Prepare(nil, nil, json.RawMessage(`{}`), nil)
	if err != nil {
		return v, err
	}
	err = c.Call(ctx, request.Service, authURL, request.Method, request.Path, bearer, request.Body, &v)
	return v, err
}
