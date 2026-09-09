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

func (v *HTTPVerifier) CreateAudience(ctx context.Context, bearer string, desired AudienceSnapshot) error {
	input := map[string]any{"id": desired.ID, "channel_id": desired.ChannelID, "group_key": desired.GroupKey, "space_ids": desired.SpaceIDs, "acknowledged_public_to_group": true}
	return v.Client.Call(ctx, "iam", v.IAMURL, "POST", "/internal/v1/channel-audiences", bearer, input, nil)
}
func (v *HTTPVerifier) UpdateAudience(ctx context.Context, bearer string, desired AudienceSnapshot, version int64) error {
	input := map[string]any{"channel_id": desired.ChannelID, "group_key": desired.GroupKey, "space_ids": desired.SpaceIDs, "base_version": version, "active": desired.Active, "acknowledged_public_to_group": true}
	return v.Client.Call(ctx, "iam", v.IAMURL, "PUT", "/internal/v1/channel-audiences/"+url.PathEscape(desired.ID), bearer, input, nil)
}

// strictAudience rejects authority receipts that are incomplete, ambiguous or
// loosely typed. A group grant must never be inferred from a partial receipt.
type strictAudience struct{ AudienceSnapshot }

func (s *strictAudience) UnmarshalJSON(b []byte) error {
	if err := rejectDuplicateKeys(b); err != nil {
		return err
	}
	var raw struct {
		ID        *string   `json:"id"`
		ChannelID *string   `json:"channel_id"`
		GroupKey  *string   `json:"group_key"`
		SpaceIDs  *[]string `json:"space_ids"`
		Active    *bool     `json:"active"`
		Version   *int64    `json:"version"`
		AuthEpoch *int64    `json:"auth_epoch"`
	}
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&raw); err != nil {
		return err
	}
	if dec.More() {
		return errors.New("trailing content in authority receipt")
	}
	if raw.ID == nil || raw.ChannelID == nil || raw.GroupKey == nil || raw.SpaceIDs == nil || raw.Active == nil || raw.Version == nil || raw.AuthEpoch == nil {
		return errors.New("incomplete authority receipt")
	}
	s.AudienceSnapshot = AudienceSnapshot{ID: *raw.ID, ChannelID: *raw.ChannelID, GroupKey: *raw.GroupKey, SpaceIDs: *raw.SpaceIDs, Active: *raw.Active, Version: *raw.Version, AuthEpoch: *raw.AuthEpoch}
	if !s.valid() {
		return errors.New("invalid authority receipt")
	}
	return nil
}

func rejectDuplicateKeys(b []byte) error {
	dec := json.NewDecoder(bytes.NewReader(b))
	return scanValue(dec)
}

func scanValue(dec *json.Decoder) error {
	t, err := dec.Token()
	if err != nil {
		return err
	}
	delim, ok := t.(json.Delim)
	if !ok {
		return nil
	}
	switch delim {
	case '{':
		seen := map[string]bool{}
		for dec.More() {
			keyToken, err := dec.Token()
			if err != nil {
				return err
			}
			name, ok := keyToken.(string)
			if !ok {
				return errors.New("malformed object key")
			}
			if seen[name] {
				return errors.New("duplicate key in authority receipt")
			}
			seen[name] = true
			if err := scanValue(dec); err != nil {
				return err
			}
		}
		if _, err := dec.Token(); err != nil {
			return err
		}
	case '[':
		for dec.More() {
			if err := scanValue(dec); err != nil {
				return err
			}
		}
		if _, err := dec.Token(); err != nil {
			return err
		}
	}
	return nil
}

func (v *HTTPVerifier) ReadAudience(ctx context.Context, bearer, id string) (out AudienceSnapshot, err error) {
	if !key(id) || bearer == "" {
		return out, ErrDenied
	}
	var receipt strictAudience
	if e := v.Client.Call(ctx, "iam", v.IAMURL, "GET", "/internal/v1/channel-audiences/"+url.PathEscape(id), bearer, nil, &receipt); e != nil {
		return out, ErrUnavailable
	}
	return receipt.AudienceSnapshot, nil
}
