package access

import (
	"sync"
	"time"
)

type Event struct {
	Path  string
	Actor string
	At    time.Time
}

type Log struct {
	mu    sync.Mutex
	last  map[string]time.Time
	items []Event
}

func New() *Log { return &Log{last: map[string]time.Time{}} }

func (l *Log) Record(path, actor string, at time.Time) {
	if l == nil {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.last == nil {
		l.last = map[string]time.Time{}
	}
	l.last[path] = at
	l.items = append(l.items, Event{Path: path, Actor: actor, At: at})
}

func (l *Log) Last(path string) (time.Time, bool) {
	if l == nil {
		return time.Time{}, false
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	t, ok := l.last[path]
	return t, ok
}

func (l *Log) All() []Event {
	if l == nil {
		return nil
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	out := make([]Event, len(l.items))
	copy(out, l.items)
	return out
}
