package audit

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"time"
)

type Log struct {
	path string
	mu   sync.Mutex
}

func New(path string) *Log { return &Log{path: path} }

func (l *Log) Record(op, actor, detail string) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if err := os.MkdirAll(filepath.Dir(l.path), 0o755); err != nil {
		return err
	}
	f, err := os.OpenFile(l.path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	row := map[string]string{
		"ts": time.Now().UTC().Format(time.RFC3339), "op": op, "actor": actor, "detail": detail,
	}
	enc := json.NewEncoder(f)
	return enc.Encode(row)
}
