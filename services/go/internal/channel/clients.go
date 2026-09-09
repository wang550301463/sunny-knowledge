package channel

import (
	"context"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"net/url"
)

type HTTPVerifier struct {
	Client   *platform.Client
	AgentURL string
	IAMURL   string
}

func (v *HTTPVerifier) Agent(ctx context.Context, bearer, id string, spaces []string) (string, error) {
	if !key(id) || len(spaces) == 0 {
		return "", ErrInvalid
	}
	var result struct {
		ID                       string `json:"id"`
		ConfigurationID          string `json:"configuration_id"`
		PublishedConfigurationID string `json:"published_configuration_id"`
		Shared                   bool   `json:"shared"`
		Config                   struct {
			SpaceIDs []string `json:"space_ids"`
		} `json:"config"`
	}
	e := v.Client.Call(ctx, "agent", v.AgentURL, "GET", "/internal/v1/agents/"+url.PathEscape(id)+"/published", bearer, nil, &result)
	if e != nil {
		return "", e
	}
	if result.ID != id || !key(result.ConfigurationID) || result.ConfigurationID != result.PublishedConfigurationID || !result.Shared || len(intersect(spaces, result.Config.SpaceIDs)) != len(spaces) {
		return "", ErrDenied
	}
	return result.ConfigurationID, nil
}
func (v *HTTPVerifier) Audience(ctx context.Context, bearer, audience, channel, group string, spaces []string) error {
	input := map[string]any{"id": audience, "channel_id": channel, "group_key": group, "space_ids": spaces, "acknowledged_public_to_group": true}
	if e := v.Client.Call(ctx, "iam", v.IAMURL, "POST", "/internal/v1/channel-audiences", bearer, input, nil); e != nil {
		return e
	}
	return v.VerifyAudience(ctx, audience, channel, group, spaces)
}
func (v *HTTPVerifier) VerifyAudience(ctx context.Context, audience, channel, group string, spaces []string) error {
	var out struct {
		Allowed   bool  `json:"allowed"`
		Version   int64 `json:"version"`
		AuthEpoch int64 `json:"auth_epoch"`
	}
	e := v.Client.Call(ctx, "iam", v.IAMURL, "POST", "/internal/v1/channel-audiences/verify", "", map[string]any{"audience_id": audience, "channel_id": channel, "group_key": group, "space_ids": spaces}, &out)
	if e != nil {
		return e
	}
	if !out.Allowed {
		return ErrDenied
	}
	return nil
}
