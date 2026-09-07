package graph

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/schema"
)

type HTTP struct {
	base    string
	groupID string
	client  *http.Client
}

func NewHTTP(base, groupID string) *HTTP {
	return &HTTP{base: strings.TrimRight(base, "/"), groupID: groupID, client: &http.Client{Timeout: 15 * time.Second}}
}

func (h *HTTP) AddEpisode(ctx context.Context, ep Episode) error {
	body := map[string]any{
		"group_id": h.groupID,
		"messages": []map[string]string{{
			"content":   ep.Name + "\n" + ep.Body,
			"role_type": "system",
			"role":      "wiki",
		}},
	}
	b, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, h.base+"/messages", bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	res, err := h.client.Do(req)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.StatusCode >= 300 {
		return fmt.Errorf("graphiti add episode: %s", res.Status)
	}
	return nil
}

func (h *HTTP) AddFact(ctx context.Context, f Fact) error {
	return h.AddEpisode(ctx, Episode{
		Name:    "fact:" + f.ID,
		Body:    f.Source + " " + string(f.Kind) + " " + f.Target,
		GroupID: h.groupID,
		Source:  "fact",
	})
}

func (h *HTTP) Supersede(ctx context.Context, newID, oldID string) error {
	return h.AddEpisode(ctx, Episode{
		Name:    "supersede:" + newID,
		Body:    newID + " supersedes " + oldID + "; previous relation is no longer current",
		GroupID: h.groupID,
		Source:  "ticket",
	})
}

func (h *HTTP) Search(ctx context.Context, query string) ([]Fact, error) {
	body := map[string]any{"group_ids": []string{h.groupID}, "query": query, "max_facts": 10}
	b, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, h.base+"/search", bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	res, err := h.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()
	if res.StatusCode >= 300 {
		return nil, fmt.Errorf("graphiti search: %s", res.Status)
	}
	var parsed struct {
		Facts []struct {
			UUID      string `json:"uuid"`
			Fact      string `json:"fact"`
			ValidAt   string `json:"valid_at"`
			InvalidAt string `json:"invalid_at"`
		} `json:"facts"`
	}
	if err := json.NewDecoder(res.Body).Decode(&parsed); err != nil {
		return nil, err
	}
	out := make([]Fact, 0, len(parsed.Facts))
	for _, f := range parsed.Facts {
		out = append(out, Fact{ID: f.UUID, Source: f.Fact, Kind: schema.EdgeUses})
	}
	return out, nil
}

func (h *HTTP) Neighborhood(ctx context.Context, center string) (Neighborhood, error) {
	facts, err := h.Search(ctx, center)
	if err != nil {
		return Neighborhood{}, err
	}
	return Neighborhood{Edges: facts, Nodes: []Node{{ID: center, Confidence: 1}}}, nil
}
