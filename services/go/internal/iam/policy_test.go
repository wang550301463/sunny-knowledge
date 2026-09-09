package iam

import (
	"github.com/wang550301463/sunny-knowledge/services/go/internal/platform"
	"testing"
)

func TestPolicyIntersectionAndNullVersusEmpty(t *testing.T) {
	p := platform.Principal{ID: "alice", Subjects: []string{"user:alice", "group:eng"}, Permissions: []string{"platform_admin"}}
	for _, tc := range []struct {
		name            string
		space, resource []string
		want            bool
	}{
		{"inherit", []string{"group:eng"}, nil, true}, {"empty resource deny", []string{"group:eng"}, []string{}, false},
		{"resource cannot broaden", []string{"group:ops"}, []string{"user:alice"}, false}, {"resource tightens", []string{"group:eng"}, []string{"group:ops"}, false},
		{"both match", []string{"group:eng"}, []string{"user:alice"}, true}, {"admin does not read", []string{}, nil, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := Allowed(p.Subjects, tc.space, tc.resource); got != tc.want {
				t.Fatalf("allowed=%v want %v", got, tc.want)
			}
		})
	}
}
func TestACLDomainCanonicalAndNullDistinct(t *testing.T) {
	if Domain("s", []string{"b", "a", "a"}, nil) != Domain("s", []string{"a", "b"}, nil) {
		t.Fatal("ordering or duplicates change domain")
	}
	if Domain("s", []string{"a"}, nil) == Domain("s", []string{"a"}, []string{}) {
		t.Fatal("null and deny share domain")
	}
	if Domain("s", []string{"a"}, nil) == Domain("t", []string{"a"}, nil) {
		t.Fatal("spaces share domain")
	}
}
