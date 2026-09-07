package ingest

import (
	"context"
	"fmt"
	"strings"

	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func ProjectWiki(ctx context.Context, st *wiki.Store, g graph.Store) error {
	pages, err := st.All()
	if err != nil {
		return err
	}
	for _, p := range pages {
		ep := graph.Episode{Name: "wiki:" + p.Path, Body: p.Meta.Title + "\n" + p.Body, GroupID: schema.DefaultGroupID, Source: "wiki"}
		if err := g.AddEpisode(ctx, ep); err != nil {
			return fmt.Errorf("project %s: %w (wiki kept)", p.Path, err)
		}
		if err := projectFileOwns(ctx, p, g); err != nil {
			return err
		}
	}
	return nil
}

func projectFileOwns(ctx context.Context, p wiki.Page, g graph.Store) error {
	if p.Meta.Type != string(schema.EntityFile) {
		return nil
	}
	for _, line := range strings.Split(p.Body, "\n") {
		line = strings.TrimSpace(strings.TrimPrefix(line, "- "))
		file, svc, ok := strings.Cut(line, " owned_by ")
		if !ok || file == "" || svc == "" {
			continue
		}
		err := g.AddFact(ctx, graph.Fact{
			ID:     "owns-" + file,
			Source: svc,
			Kind:   schema.EdgeOwns,
			Target: file,
		})
		if err != nil {
			return err
		}
	}
	return nil
}
