package evolve

import (
	"fmt"
	"sync"
	"time"
)

type Kind string

const (
	KindMergeEntity   Kind = "merge_entity"
	KindChangeSchema  Kind = "change_schema"
	KindOverrideClaim Kind = "override_claim"
	KindWriteConflict Kind = "write_conflict"
)

type Item struct {
	ID       string
	Kind     Kind
	Summary  string
	Done     bool
	Approved bool
	Actor    string
	Created  time.Time
}

type Queue struct {
	mu    sync.Mutex
	items []Item
	seq   int
}

func NewQueue() *Queue { return &Queue{} }

func (q *Queue) Enqueue(it Item) string {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.seq++
	it.ID = fmt.Sprintf("rv-%d", q.seq)
	it.Created = time.Now().UTC()
	q.items = append(q.items, it)
	return it.ID
}

func (q *Queue) Open() []Item {
	q.mu.Lock()
	defer q.mu.Unlock()
	var out []Item
	for _, it := range q.items {
		if !it.Done {
			out = append(out, it)
		}
	}
	return out
}

func (q *Queue) Resolve(id string, approved bool, actor string) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	for i := range q.items {
		if q.items[i].ID == id {
			q.items[i].Done = true
			q.items[i].Approved = approved
			q.items[i].Actor = actor
			return nil
		}
	}
	return fmt.Errorf("review %s not found", id)
}
