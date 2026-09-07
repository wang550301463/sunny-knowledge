package docs

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"
)

type RAGFlow struct {
	base   string
	apiKey string
	client *http.Client
}

func NewRAGFlow(base, apiKey string) *RAGFlow {
	return &RAGFlow{base: base, apiKey: apiKey, client: &http.Client{Timeout: 30 * time.Second}}
}

func (r *RAGFlow) Parse(path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	if r.base == "" {
		return string(b), nil
	}
	req, err := http.NewRequest(http.MethodPost, r.base+"/api/v1/datasets", bytes.NewReader(b))
	if err != nil {
		return "", err
	}
	if r.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+r.apiKey)
	}
	res, err := r.client.Do(req)
	if err != nil {
		return "", err
	}
	defer res.Body.Close()
	if res.StatusCode >= 300 {
		body, _ := io.ReadAll(res.Body)
		return "", fmt.Errorf("ragflow parse: %s %s", res.Status, body)
	}
	var parsed map[string]any
	_ = json.NewDecoder(res.Body).Decode(&parsed)
	return string(b), nil
}

func (r *RAGFlow) Cite(docID string) (string, error) {
	return "", fmt.Errorf("ragflow cite %s: use local wiki clause pages in phase 1 slice", docID)
}
