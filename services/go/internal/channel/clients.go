package channel

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
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
func audienceError(e error) error {
	var upstream *platform.HTTPError
	if errors.As(e, &upstream) {
		switch upstream.Status {
		case 404:
			return ErrNotFound
		case 409:
			return ErrConflict
		case 400, 422:
			return ErrInvalid
		case 401, 403:
			return ErrDenied
		}
	}
	if e != nil {
		return ErrUnavailable
	}
	return nil
}
func (v *HTTPVerifier) ReadAudience(ctx context.Context, bearer, id string) (AudienceSnapshot, error) {
	if !key(id) {
		return AudienceSnapshot{}, ErrInvalid
	}
	var raw json.RawMessage
	if e := v.Client.Call(ctx, "iam", v.IAMURL, "GET", "/internal/v1/channel-audiences/"+url.PathEscape(id), bearer, nil, &raw); e != nil {
		return AudienceSnapshot{}, audienceError(e)
	}
	var out AudienceSnapshot
	var fields map[string]json.RawMessage
	if strictJSON(raw, &fields) != nil || len(fields) != 7 {
		return out, ErrUnavailable
	}
	for _, name := range []string{"id", "channel_id", "group_key", "space_ids", "active", "version", "auth_epoch"} {
		if len(fields[name]) == 0 || bytes.Equal(fields[name], []byte("null")) {
			return out, ErrUnavailable
		}
	}
	// Seven required fields; unknown metadata cannot become an authority receipt.
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&out) != nil || !out.valid() || out.ID != id {
		return AudienceSnapshot{}, ErrUnavailable
	}
	return out, nil
}
func (v *HTTPVerifier) CreateAudience(ctx context.Context, bearer string, in AudienceSnapshot) error {
	input := map[string]any{"id": in.ID, "channel_id": in.ChannelID, "group_key": in.GroupKey, "space_ids": in.SpaceIDs, "acknowledged_public_to_group": true}
	return audienceError(v.Client.Call(ctx, "iam", v.IAMURL, "POST", "/internal/v1/channel-audiences", bearer, input, nil))
}
func (v *HTTPVerifier) UpdateAudience(ctx context.Context, bearer string, in AudienceSnapshot, base int64) error {
	input := map[string]any{"base_version": base, "channel_id": in.ChannelID, "group_key": in.GroupKey, "space_ids": in.SpaceIDs, "active": in.Active, "acknowledged_public_to_group": in.Active}
	return audienceError(v.Client.Call(ctx, "iam", v.IAMURL, "PUT", "/internal/v1/channel-audiences/"+url.PathEscape(in.ID), bearer, input, nil))
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
