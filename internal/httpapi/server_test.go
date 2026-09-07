package httpapi_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/docs"
	"github.com/wang550301463/sunny-knowledge/internal/evolve"
	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/httpapi"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

func TestAskAndNeighborhoodAndReviewAuth(t *testing.T) {
	st := wiki.NewStore(t.TempDir())
	_ = st.Write(wiki.Page{Path: "procedures/change.md", Meta: wiki.Meta{Title: "变更流程", Type: string(schema.EntityProcedure), Source: "code"}, Body: "提 PR 后发布"})
	g := graph.NewFake()
	_ = g.AddFact(context.Background(), graph.Fact{ID: "e1", Source: "pay-api", Kind: schema.EdgeOwns, Target: "internal/refund/refund.go"})
	q := evolve.NewQueue()
	srv := httpapi.New(httpapi.Deps{Wiki: st, Graph: g, Cite: docs.NewFake(), Reviews: q, Token: "secret"})
	ts := httptest.NewServer(srv)
	defer ts.Close()

	req, _ := http.NewRequest(http.MethodPost, ts.URL+"/v1/ask", strings.NewReader(`{"question":"我们怎么做变更"}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Token", "secret")
	req.Header.Set("X-Role", "editor")
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	if res.StatusCode != 200 {
		t.Fatalf("status %d", res.StatusCode)
	}
	var ans map[string]any
	_ = json.NewDecoder(res.Body).Decode(&ans)
	text, _ := ans["text"].(string)
	if !strings.Contains(text, "PR") {
		t.Fatalf("%v", ans)
	}

	nreq, _ := http.NewRequest(http.MethodGet, ts.URL+"/v1/graph/neighborhood?center=pay-api", nil)
	nreq.Header.Set("X-Token", "secret")
	nreq.Header.Set("X-Role", "reader")
	nres, _ := http.DefaultClient.Do(nreq)
	if nres.StatusCode != 200 {
		t.Fatalf("nb %d", nres.StatusCode)
	}

	bad, _ := http.NewRequest(http.MethodGet, ts.URL+"/v1/reviews", nil)
	bad.Header.Set("X-Token", "secret")
	bad.Header.Set("X-Role", "reader")
	bres, _ := http.DefaultClient.Do(bad)
	if bres.StatusCode != 403 {
		t.Fatalf("want 403 got %d", bres.StatusCode)
	}
}
