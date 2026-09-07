package wiki

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

type Meta struct {
	Title          string    `json:"title"`
	Type           string    `json:"type"`
	Source         string    `json:"source"`
	Stale          bool      `json:"stale"`
	Superseded     string    `json:"superseded"`
	Confidence     float64   `json:"confidence"`
	Scope          string    `json:"scope"`
	LastAccessed   time.Time `json:"last_accessed"`
	LastReinforced time.Time `json:"last_reinforced"`
}

type Page struct {
	Path string `json:"path"`
	Meta Meta   `json:"meta"`
	Body string `json:"body"`
}

func (m Meta) withDefaults() Meta {
	if m.Scope == "" {
		m.Scope = "shared"
	}
	return m
}

func (p Page) Render() string {
	m := p.Meta.withDefaults()
	conf := m.Confidence
	if conf == 0 && m.LastReinforced.IsZero() {
		conf = 1
	}
	stale := "false"
	if m.Stale {
		stale = "true"
	}
	acc, rei := "", ""
	if !m.LastAccessed.IsZero() {
		acc = m.LastAccessed.UTC().Format(time.RFC3339)
	}
	if !m.LastReinforced.IsZero() {
		rei = m.LastReinforced.UTC().Format(time.RFC3339)
	}
	return fmt.Sprintf("---\ntitle: %s\ntype: %s\nsource: %s\nstale: %s\nsuperseded: %s\nconfidence: %g\nscope: %s\nlast_accessed: %s\nlast_reinforced: %s\n---\n\n%s\n",
		m.Title, m.Type, m.Source, stale, m.Superseded,
		conf, m.Scope, acc, rei, strings.TrimSpace(p.Body))
}

func Parse(path, raw string) (Page, error) {
	p := Page{Path: path, Meta: Meta{Confidence: 1, Scope: "shared"}}
	if !strings.HasPrefix(raw, "---\n") {
		p.Body = raw
		return p, nil
	}
	rest := strings.TrimPrefix(raw, "---\n")
	idx := strings.Index(rest, "\n---\n")
	if idx < 0 {
		return p, fmt.Errorf("unterminated frontmatter in %s", path)
	}
	fm, body := rest[:idx], rest[idx+5:]
	p.Body = strings.TrimSpace(body)
	confSet := false
	for _, line := range strings.Split(fm, "\n") {
		k, v, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		v = strings.TrimSpace(v)
		switch strings.TrimSpace(k) {
		case "title":
			p.Meta.Title = v
		case "type":
			p.Meta.Type = v
		case "source":
			p.Meta.Source = v
		case "stale":
			p.Meta.Stale = v == "true"
		case "superseded":
			p.Meta.Superseded = v
		case "confidence":
			if v != "" {
				if f, err := strconv.ParseFloat(v, 64); err == nil {
					p.Meta.Confidence = f
					confSet = true
				}
			}
		case "scope":
			if v != "" {
				p.Meta.Scope = v
			}
		case "last_accessed":
			if v != "" {
				if t, err := time.Parse(time.RFC3339, v); err == nil {
					p.Meta.LastAccessed = t
				}
			}
		case "last_reinforced":
			if v != "" {
				if t, err := time.Parse(time.RFC3339, v); err == nil {
					p.Meta.LastReinforced = t
				}
			}
		}
	}
	if !confSet {
		p.Meta.Confidence = 1
	}
	if p.Meta.Scope == "" {
		p.Meta.Scope = "shared"
	}
	return p, nil
}
