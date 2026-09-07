package graph_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/schema"
)

func TestHTTPAddEpisodePostsMessages(t *testing.T) {
	var got map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/messages" {
			t.Fatalf("path %s", r.URL.Path)
		}
		b, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(b, &got)
		w.WriteHeader(202)
	}))
	defer srv.Close()
	c := graph.NewHTTP(srv.URL, schema.DefaultGroupID)
	err := c.AddEpisode(context.Background(), graph.Episode{Name: "wiki:pay-api", Body: "pay-api uses redis", Source: "text"})
	if err != nil {
		t.Fatal(err)
	}
	if got["group_id"] != schema.DefaultGroupID {
		t.Fatalf("%v", got)
	}
}
