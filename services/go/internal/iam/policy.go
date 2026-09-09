package iam

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"sort"
)

func Canonical(subjects []string) []string {
	if subjects == nil {
		return nil
	}
	out := append([]string{}, subjects...)
	sort.Strings(out)
	n := 0
	for _, s := range out {
		if n == 0 || out[n-1] != s {
			out[n] = s
			n++
		}
	}
	return out[:n]
}
func Allowed(subjects, space, resource []string) bool {
	has := func(acl []string) bool {
		for _, a := range acl {
			for _, s := range subjects {
				if a == s {
					return true
				}
			}
		}
		return false
	}
	return has(space) && (resource == nil || has(resource))
}

// Canonical UTF-8 JSON uses lexicographically sorted keys and sorted unique subjects.
func Domain(space string, subjects, resource []string) string {
	if subjects == nil {
		subjects = []string{}
	}
	b, _ := json.Marshal(map[string]any{"space_id": space, "space_read_subjects": Canonical(subjects), "resource_read_subjects": Canonical(resource)})
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}
