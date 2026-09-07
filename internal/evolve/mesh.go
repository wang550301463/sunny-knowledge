package evolve

import (
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/wang550301463/sunny-knowledge/internal/wiki"
)

type lock struct {
	Agent string
	Until time.Time
}

type Mesh struct {
	st    *wiki.Store
	q     *Queue
	mu    sync.Mutex
	locks map[string]lock
	mtime map[string]time.Time
}

func NewMesh(st *wiki.Store, q *Queue) *Mesh {
	return &Mesh{st: st, q: q, locks: map[string]lock{}, mtime: map[string]time.Time{}}
}

func (m *Mesh) Lock(path, agent string, ttl time.Duration) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	now := time.Now()
	if l, ok := m.locks[path]; ok && l.Until.After(now) && l.Agent != agent {
		return fmt.Errorf("locked by %s", l.Agent)
	}
	m.locks[path] = lock{Agent: agent, Until: now.Add(ttl)}
	return nil
}

func (m *Mesh) Write(agent string, p wiki.Page, at time.Time) error {
	if p.Meta.Scope == "private" && !strings.HasPrefix(p.Path, "private/"+agent+"/") {
		return fmt.Errorf("private pages must live under private/%s/", agent)
	}
	m.mu.Lock()
	l, locked := m.locks[p.Path]
	now := time.Now()
	if locked && l.Until.After(now) && l.Agent != agent {
		m.mu.Unlock()
		return fmt.Errorf("locked by %s", l.Agent)
	}
	prevTime, existed := m.mtime[p.Path]
	m.mu.Unlock()

	if existed {
		old, err := m.st.Read(p.Path)
		if err == nil && old.Body != p.Body {
			m.q.Enqueue(Item{Kind: KindWriteConflict, Summary: fmt.Sprintf("%s vs %s on %s", agent, old.Body, p.Path)})
		}
		if at.Before(prevTime) {
			return nil
		}
	}
	if err := m.st.Write(p); err != nil {
		return err
	}
	m.mu.Lock()
	m.mtime[p.Path] = at
	m.mu.Unlock()
	return nil
}

func (m *Mesh) Promote(path, actor string) error {
	p, err := m.st.Read(path)
	if err != nil {
		return err
	}
	if p.Meta.Scope != "private" {
		return fmt.Errorf("%s is not private", path)
	}
	name := path
	if i := strings.LastIndex(path, "/"); i >= 0 {
		name = path[i+1:]
	}
	p.Path = "entities/" + name
	p.Meta.Scope = "shared"
	_ = actor
	return m.st.Write(p)
}
