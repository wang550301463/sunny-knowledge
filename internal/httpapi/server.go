package httpapi

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/access"
	"github.com/wang550301463/sunny-knowledge/internal/crystal"
	"github.com/wang550301463/sunny-knowledge/internal/docs"
	"github.com/wang550301463/sunny-knowledge/internal/evolve"
	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/query"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

type Deps struct {
	Wiki    *wiki.Store
	Graph   graph.Store
	Cite    docs.Store
	Reviews *evolve.Queue
	Mesh    *evolve.Mesh
	Access  *access.Log
	Token   string
}

func New(d Deps) http.Handler {
	if d.Access == nil {
		d.Access = access.New()
	}
	if d.Mesh == nil && d.Wiki != nil && d.Reviews != nil {
		d.Mesh = evolve.NewMesh(d.Wiki, d.Reviews)
	}
	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/ask", requireAuth(d.Token, d.Token, []string{"editor", "admin"}, func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Question string `json:"question"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		ans, err := query.Ask(r.Context(), query.Request{Question: body.Question, Actor: r.Header.Get("X-Role")}, d.Wiki, d.Graph, d.Cite, d.Access)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		_ = crystal.MaybeFile(r.Context(), ans, 0.9, d.Wiki, d.Graph)
		writeJSON(w, map[string]any{"text": ans.Text, "pages": ans.Pages, "usedCitation": ans.UsedCitation, "graphDegraded": ans.GraphDegraded})
	}))
	mux.HandleFunc("GET /v1/wiki/", requireAuth(d.Token, d.Token, []string{"reader", "editor", "admin"}, func(w http.ResponseWriter, r *http.Request) {
		rel := strings.TrimPrefix(r.URL.Path, "/v1/wiki/")
		p, err := d.Wiki.Read(rel)
		if err != nil {
			http.Error(w, err.Error(), 404)
			return
		}
		writeJSON(w, p)
	}))
	mux.HandleFunc("GET /v1/graph/neighborhood", requireAuth(d.Token, d.Token, []string{"reader", "editor", "admin"}, func(w http.ResponseWriter, r *http.Request) {
		center := r.URL.Query().Get("center")
		nb, err := d.Graph.Neighborhood(r.Context(), center)
		if err != nil {
			http.Error(w, err.Error(), 503)
			return
		}
		pages, _ := d.Wiki.All()
		conf := map[string]float64{}
		for _, p := range pages {
			conf[p.Meta.Title] = p.Meta.Confidence
			conf[strings.TrimSuffix(p.Path, ".md")] = p.Meta.Confidence
		}
		type nodeJSON struct {
			ID         string  `json:"id"`
			Kind       string  `json:"kind"`
			Confidence float64 `json:"confidence"`
		}
		type edgeJSON struct {
			ID         string  `json:"id"`
			Source     string  `json:"source"`
			Target     string  `json:"target"`
			Kind       string  `json:"kind"`
			InvalidAt  *string `json:"invalidAt"`
			Origin     string  `json:"origin"`
			SourceKind string  `json:"sourceKind"`
		}
		nodes := make([]nodeJSON, 0, len(nb.Nodes))
		for _, n := range nb.Nodes {
			c := n.Confidence
			if v, ok := conf[n.ID]; ok {
				c = v
			}
			if c == 0 {
				c = 1
			}
			kind := string(n.Kind)
			if kind == "" && (strings.Contains(n.ID, "/") || strings.Contains(n.ID, ".")) {
				kind = string(schema.EntityFile)
			}
			if kind == "" {
				kind = string(schema.EntityService)
			}
			nodes = append(nodes, nodeJSON{ID: n.ID, Kind: kind, Confidence: c})
		}
		edges := make([]edgeJSON, 0, len(nb.Edges))
		for _, e := range nb.Edges {
			var inv *string
			if e.InvalidAt != nil {
				s := e.InvalidAt.UTC().Format(time.RFC3339)
				inv = &s
			}
			srcKind := "graph"
			switch e.Kind {
			case schema.EdgeOwns:
				srcKind = "code"
			case schema.EdgeUses:
				srcKind = "ticket"
			}
			edges = append(edges, edgeJSON{
				ID: e.ID, Source: e.Source, Target: e.Target, Kind: string(e.Kind), InvalidAt: inv, Origin: "graph", SourceKind: srcKind,
			})
		}
		writeJSON(w, map[string]any{"nodes": nodes, "edges": edges})
	}))
	mux.HandleFunc("GET /v1/reviews", requireAuth(d.Token, d.Token, []string{"admin"}, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, d.Reviews.Open())
	}))
	mux.HandleFunc("POST /v1/reviews/", requireAuth(d.Token, d.Token, []string{"admin"}, func(w http.ResponseWriter, r *http.Request) {
		id := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/v1/reviews/"), "/resolve")
		id = strings.TrimSuffix(id, "/")
		if !strings.HasSuffix(r.URL.Path, "/resolve") {
			http.NotFound(w, r)
			return
		}
		var body struct {
			Approved bool `json:"approved"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		if err := d.Reviews.Resolve(id, body.Approved, "admin"); err != nil {
			http.Error(w, err.Error(), 404)
			return
		}
		writeJSON(w, map[string]string{"ok": "true"})
	}))
	mux.HandleFunc("POST /v1/wiki/lock", requireAuth(d.Token, d.Token, []string{"editor", "admin"}, func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Path       string `json:"path"`
			Agent      string `json:"agent"`
			TTLSeconds int    `json:"ttl_seconds"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body.TTLSeconds <= 0 {
			body.TTLSeconds = 300
		}
		if err := d.Mesh.Lock(body.Path, body.Agent, time.Duration(body.TTLSeconds)*time.Second); err != nil {
			http.Error(w, err.Error(), 409)
			return
		}
		writeJSON(w, map[string]string{"ok": "true"})
	}))
	mux.HandleFunc("POST /v1/wiki/promote", requireAuth(d.Token, d.Token, []string{"editor", "admin"}, func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Path string `json:"path"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		if err := d.Mesh.Promote(body.Path, trimRole(r)); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		writeJSON(w, map[string]string{"ok": "true"})
	}))
	return mux
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
