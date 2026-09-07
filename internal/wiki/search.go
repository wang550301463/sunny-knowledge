package wiki

import (
	"math"
	"sort"
	"strings"
	"unicode"
)

type Hit struct {
	Path  string
	Score float64
}

func tokenize(s string) []string {
	s = strings.ToLower(s)
	parts := strings.FieldsFunc(s, func(r rune) bool {
		return unicode.IsSpace(r) || unicode.IsPunct(r) || unicode.IsSymbol(r)
	})
	var out []string
	for _, p := range parts {
		if p == "" {
			continue
		}
		out = append(out, p)
		rs := []rune(p)
		han := false
		for _, r := range rs {
			if unicode.Is(unicode.Han, r) {
				han = true
				break
			}
		}
		if !han {
			continue
		}
		for i := 0; i < len(rs); i++ {
			out = append(out, string(rs[i]))
			if i+1 < len(rs) {
				out = append(out, string(rs[i:i+2]))
			}
		}
	}
	return out
}

func (s *Store) Search(q string) []Hit {
	pages, err := s.All()
	if err != nil {
		return nil
	}
	qtoks := tokenize(q)
	df := map[string]int{}
	docs := make([]map[string]int, len(pages))
	for i, p := range pages {
		tf := map[string]int{}
		for _, t := range tokenize(p.Meta.Title + " " + p.Body) {
			tf[t]++
		}
		docs[i] = tf
		seen := map[string]struct{}{}
		for t := range tf {
			if _, ok := seen[t]; ok {
				continue
			}
			seen[t] = struct{}{}
			df[t]++
		}
	}
	n := float64(len(pages))
	var hits []Hit
	for i, p := range pages {
		var score float64
		for _, t := range qtoks {
			tf := float64(docs[i][t])
			if tf == 0 {
				continue
			}
			idf := math.Log((n+1)/(float64(df[t])+1)) + 1
			score += tf * idf
		}
		if p.Meta.Stale {
			score *= 0.2
		}
		conf := p.Meta.Confidence
		if conf == 0 && p.Meta.LastReinforced.IsZero() {
			conf = 1
		}
		score *= conf
		if score > 0 {
			hits = append(hits, Hit{Path: p.Path, Score: score})
		}
	}
	sort.Slice(hits, func(i, j int) bool { return hits[i].Score > hits[j].Score })
	return hits
}
