package graph

import "context"

type Store interface {
	AddEpisode(ctx context.Context, ep Episode) error
	AddFact(ctx context.Context, f Fact) error
	Supersede(ctx context.Context, newID, oldID string) error
	Search(ctx context.Context, query string) ([]Fact, error)
	Neighborhood(ctx context.Context, center string) (Neighborhood, error)
}
