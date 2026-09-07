package ingest

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/secret"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

type ticket struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	Body  string `json:"body"`
	At    string `json:"at"`
}

func ApplyTicket(ctx context.Context, raw []byte, st *wiki.Store, g graph.Store) error {
	var tk ticket
	if err := json.Unmarshal(raw, &tk); err != nil {
		return err
	}
	body := secret.Strip(tk.Title + "\n" + tk.Body)
	if err := g.AddEpisode(ctx, graph.Episode{Name: tk.ID, Body: body, GroupID: schema.DefaultGroupID, Source: "ticket"}); err != nil {
		return err
	}
	at := time.Now().UTC()
	if tk.At != "" {
		if parsed, err := time.Parse(time.RFC3339, tk.At); err == nil {
			at = parsed
		}
	}
	newID := "e-" + tk.ID
	_ = g.AddFact(ctx, graph.Fact{ID: newID, Source: "pay-api", Kind: schema.EdgeUses, Target: "redis-7", ValidAt: at, Episode: tk.ID})
	if strings.Contains(strings.ToLower(body), "supersedes") {
		if err := g.Supersede(ctx, newID, "e-old"); err != nil {
			return err
		}
		p, err := st.Read("entities/pay-api.md")
		if err != nil {
			return err
		}
		p.Meta.Stale = true
		p.Meta.Superseded = tk.ID
		p.Body += fmt.Sprintf("\n\n被 %s 取代。\n", tk.ID)
		if err := st.Write(p); err != nil {
			return err
		}
	}
	return st.Write(wiki.Page{
		Path: "summaries/" + tk.ID + ".md",
		Meta: wiki.Meta{Title: tk.Title, Type: string(schema.EntityIncident), Source: "ticket", Confidence: 1, Scope: "shared"},
		Body: body,
	})
}
