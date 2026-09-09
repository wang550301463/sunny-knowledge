package iam

import (
	"context"
	"net/http"

	"github.com/jackc/pgx/v5"
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
)

type PolicyItem struct {
	SpaceID    string  `json:"space_id"`
	ResourceID string  `json:"resource_id"`
	Found      bool    `json:"found"`
	Policy     *Policy `json:"policy,omitempty"`
}
type PolicyBatch struct {
	AuthEpoch int64        `json:"auth_epoch"`
	Items     []PolicyItem `json:"items"`
}

// Policies returns all ACL provenance from one read snapshot, including missing resources.
// It is an internal projection capability, not a delegated content-read API.
func (s *Store) Policies(ctx context.Context, resources []platform.Resource) (out PolicyBatch, err error) {
	if len(resources) == 0 || len(resources) > 1000 {
		return out, ErrInvalid
	}
	spaces, ids := make([]string, len(resources)), make([]string, len(resources))
	seen := make(map[platform.Resource]bool, len(resources))
	for i, r := range resources {
		if !validID(r.SpaceID) || (r.ResourceID != "" && !validID(r.ResourceID)) || seen[r] {
			return out, ErrInvalid
		}
		seen[r] = true
		spaces[i], ids[i] = r.SpaceID, r.ResourceID
	}
	out.Items = make([]PolicyItem, 0, len(resources))
	err = s.read(ctx, func(tx pgx.Tx) error {
		var err error
		out.AuthEpoch, err = epoch(ctx, tx)
		if err != nil {
			return err
		}
		rows, err := tx.Query(ctx, `WITH requested AS (
   SELECT * FROM unnest($1::text[],$2::text[]) WITH ORDINALITY AS r(space_id,resource_id,ord)
  ) SELECT r.space_id,r.resource_id,
    s.id IS NOT NULL AND sp.space_id IS NOT NULL AND (r.resource_id='' OR rr.id IS NOT NULL),
    sp.subjects,rp.subjects,GREATEST(COALESCE(sp.version,0),COALESCE(rp.version,0))
  FROM requested r
  LEFT JOIN iam_spaces s ON s.id=r.space_id
  LEFT JOIN iam_resources rr ON rr.space_id=r.space_id AND rr.id=r.resource_id
  LEFT JOIN iam_policies sp ON sp.space_id=r.space_id AND sp.resource_id='' AND sp.action='read'
  LEFT JOIN iam_policies rp ON rp.space_id=r.space_id AND rp.resource_id=r.resource_id AND r.resource_id<>'' AND rp.action='read'
  ORDER BY r.ord`, spaces, ids)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var item PolicyItem
			var p Policy
			if err = rows.Scan(&item.SpaceID, &item.ResourceID, &item.Found, &p.SpaceReadSubjects, &p.ResourceReadSubjects, &p.ACLVersion); err != nil {
				return err
			}
			if item.Found {
				p.SpaceID, p.ResourceID, p.AuthEpoch = item.SpaceID, item.ResourceID, out.AuthEpoch
				p.ACLDomain = Domain(p.SpaceID, p.SpaceReadSubjects, p.ResourceReadSubjects)
				item.Policy = &p
			}
			out.Items = append(out.Items, item)
		}
		return rows.Err()
	})
	return
}

func (h *Handler) policyBatch(w http.ResponseWriter, r *http.Request) {
	if !caller(w, r, "retrieval", "graphiti") {
		return
	}
	var in struct {
		Resources []platform.Resource `json:"resources"`
	}
	if !decode(w, r, &in) {
		return
	}
	result, err := h.Store.Policies(r.Context(), in.Resources)
	if err != nil {
		storeError(w, r, err)
		return
	}
	platform.JSON(w, 200, result)
}
