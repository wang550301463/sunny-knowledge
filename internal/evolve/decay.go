package evolve

import (
	"math"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func halfLifeDays(kind string) float64 {
	switch schema.EntityKind(kind) {
	case schema.EntityPolicy, schema.EntityDecision, schema.EntityProcedure:
		return 180
	case schema.EntityFile, schema.EntityIncident, schema.EntityChange:
		return 21
	default:
		return 90
	}
}

func Decay(confidence float64, kind string, lastReinforced, now time.Time) float64 {
	if confidence <= 0 {
		return 0
	}
	if lastReinforced.IsZero() {
		lastReinforced = now
	}
	days := now.Sub(lastReinforced).Hours() / 24
	if days < 0 {
		days = 0
	}
	hl := halfLifeDays(kind)
	out := confidence * math.Pow(0.5, days/hl)
	if out < 0 {
		return 0
	}
	if out > 1 {
		return 1
	}
	return out
}

type ReinforceResult struct {
	Confidence float64
	At         time.Time
}

func Reinforce(confidence float64, at time.Time) ReinforceResult {
	c := confidence + 0.1
	if c > 1 {
		c = 1
	}
	return ReinforceResult{Confidence: c, At: at}
}

func Apply(st *wiki.Store, now time.Time) (int, error) {
	pages, err := st.All()
	if err != nil {
		return 0, err
	}
	n := 0
	for _, p := range pages {
		from := p.Meta.LastReinforced
		if from.IsZero() {
			from = p.Meta.LastAccessed
		}
		next := Decay(p.Meta.Confidence, p.Meta.Type, from, now)
		if next == p.Meta.Confidence {
			continue
		}
		p.Meta.Confidence = next
		if err := st.Write(p); err != nil {
			return n, err
		}
		n++
	}
	return n, nil
}
