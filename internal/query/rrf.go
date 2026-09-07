package query

import "sort"

func Fuse(ranked [][]string, weights []float64, k int) []string {
	score := map[string]float64{}
	for i, list := range ranked {
		w := 1.0
		if i < len(weights) {
			w = weights[i]
		}
		for rank, id := range list {
			score[id] += w / (float64(k) + float64(rank+1))
		}
	}
	type pair struct {
		id string
		sc float64
	}
	var ps []pair
	for id, sc := range score {
		ps = append(ps, pair{id, sc})
	}
	sort.Slice(ps, func(i, j int) bool { return ps[i].sc > ps[j].sc })
	out := make([]string, len(ps))
	for i, p := range ps {
		out[i] = p.id
	}
	return out
}
