package query

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/access"
	"github.com/wang550301463/sunny-knowledge/internal/docs"
	"github.com/wang550301463/sunny-knowledge/internal/graph"
	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

type Request struct {
	Question string
	Actor    string
}

type Answer struct {
	Text          string
	Pages         []string
	Facts         []graph.Fact
	UsedCitation  bool
	GraphDegraded bool
}

func Ask(ctx context.Context, req Request, st *wiki.Store, g graph.Store, cite docs.Store, acc *access.Log) (Answer, error) {
	kind := Classify(req.Question)
	hits := st.Search(req.Question)
	var pageIDs []string
	var bodies []string
	now := time.Now().UTC()
	for i, h := range hits {
		if i >= 5 {
			break
		}
		pageIDs = append(pageIDs, h.Path)
		p, err := st.Read(h.Path)
		if err != nil {
			continue
		}
		acc.Record(p.Path, req.Actor, now)
		p.Meta.LastAccessed = now
		_ = st.Write(p)
		prefix := ""
		if p.Meta.Stale {
			prefix = "【已过期】"
		}
		bodies = append(bodies, prefix+p.Body)
	}
	facts, err := g.Search(ctx, req.Question)
	degraded := false
	if err != nil {
		degraded = true
		facts = nil
	}
	var live []string
	for _, f := range facts {
		if f.InvalidAt == nil {
			live = append(live, f.Source+" "+string(f.Kind)+" "+f.Target)
		}
	}
	fused := Fuse([][]string{pageIDs, live}, []float64{1.0, 0.8}, 60)
	ans := Answer{Pages: pageIDs, Facts: facts, GraphDegraded: degraded}
	ans.Text = strings.Join(bodies, "\n")
	if len(live) > 0 {
		ans.Text += "\n现行关系：" + strings.Join(live, "; ")
	}
	if degraded {
		ans.Text += "\n（图未就绪，仅 wiki）"
	}
	if kind == KindCite {
		c, err := cite.Cite("policy-refund")
		if err == nil {
			ans.UsedCitation = true
			ans.Text += "\n原文：" + c
		}
	}
	if ans.Text == "" {
		ans.Text = fmt.Sprintf("无编译页命中：%v", fused)
	}
	return ans, nil
}
