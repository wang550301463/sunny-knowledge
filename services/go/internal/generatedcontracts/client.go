// Package generatedcontracts prepares requests for the existing authenticated transport.
// It never chooses service URLs, signs credentials, or caches identity or content.
package generatedcontracts

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/url"
	"sort"
	"strings"
)

type Operation struct {
	ID, Service, Method, Template, ResponseTyping, Transport                    string
	PathParameters, QueryParameters, HeaderParameters, RequiredHeaderParameters []string
	BodyRequired, AcceptsBody                                                   bool
	RequestSchema, ResponseSchema                                               string
}

type Prepared struct {
	Service, Method, Path string
	Body                  json.RawMessage
	Headers               map[string]string
}

func members(values []string) map[string]bool {
	found := map[string]bool{}
	for _, v := range values {
		found[v] = true
	}
	return found
}

// Prepare preserves nullable JSON and literal identifiers. It does not replace
// the server's native validation or the transport's fresh workload/user identity.
func (operation Operation) Prepare(path map[string]string, query url.Values, body json.RawMessage, headers map[string]string) (Prepared, error) {
	fail := func() (Prepared, error) { return Prepared{}, errors.New("request does not match declared operation") }
	if len(path) != len(operation.PathParameters) {
		return fail()
	}
	target := operation.Template
	for _, key := range operation.PathParameters {
		value, ok := path[key]
		if !ok || value == "" || value == "." || value == ".." {
			return fail()
		}
		// QueryEscape escapes every reserved character; use %20 rather than + for paths.
		target = strings.ReplaceAll(target, "{"+key+"}", strings.ReplaceAll(url.QueryEscape(value), "+", "%20"))
	}
	if !strings.HasPrefix(target, "/") || strings.HasPrefix(target, "//") || strings.Contains(target, "{") {
		return fail()
	}
	permitted := members(operation.QueryParameters)
	for key := range query {
		if !permitted[key] {
			return fail()
		}
	}
	if len(query) > 0 {
		target += "?" + query.Encode()
	}
	if operation.BodyRequired && len(body) == 0 || !operation.AcceptsBody && len(body) > 0 {
		return fail()
	}
	if len(body) > 0 && !json.Valid(body) {
		return fail()
	}
	permitted = map[string]bool{}
	for _, name := range operation.HeaderParameters {
		permitted[strings.ToLower(name)] = true
	}
	received := map[string]bool{}
	for name := range headers {
		lower := strings.ToLower(name)
		if received[lower] {
			return fail()
		}
		received[lower] = true
	}
	for _, name := range operation.RequiredHeaderParameters {
		if !received[strings.ToLower(name)] {
			return fail()
		}
	}
	resultHeaders := map[string]string{}
	keys := []string{}
	for key := range headers {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		lower := strings.ToLower(key)
		value := headers[key]
		if !permitted[lower] || lower == "authorization" || lower == "x-service-token" || lower == "cookie" || lower == "host" || strings.ContainsAny(key+value, "\r\n") {
			return fail()
		}
		resultHeaders[key] = value
	}
	return Prepared{operation.Service, operation.Method, target, bytes.Clone(body), resultHeaders}, nil
}
