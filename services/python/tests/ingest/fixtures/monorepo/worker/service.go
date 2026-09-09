package worker

import (
	"context"
	db "example.com/storage/client"
)

type Service struct{ Client *db.Client }

func (s *Service) Find(
	ctx context.Context,
	query string,
) (string, error) {
	return query, nil
}

func New() *Service { return &Service{} }
