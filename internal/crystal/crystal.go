package crystal

import (
	"context"
	"fmt"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/query"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

const MinQuality = 0.7

func MaybeFile(ctx context.Context, ans query.Answer, quality float64, st *wiki.Store, g graph.Store) error {
	if quality < MinQuality {
		return nil
	}
	now := time.Now().UTC()
	p := wiki.Page{
		Path: "summaries/digest-latest.md",
		Meta: wiki.Meta{
			Title:          "结晶",
			Type:           string(schema.EntityDecision),
			Source:         "crystal",
			Confidence:     1,
			Scope:          "shared",
			LastReinforced: now,
		},
		Body: fmt.Sprintf("%s\n\n来源页：%v", ans.Text, ans.Pages),
	}
	if err := st.Write(p); err != nil {
		return err
	}
	return g.AddEpisode(ctx, graph.Episode{Name: "crystal:digest-latest", Body: p.Body, GroupID: schema.DefaultGroupID, Source: "crystal"})
}
