package query

import "strings"

type Kind string

const (
	KindStructure Kind = "structure"
	KindTemporal  Kind = "temporal"
	KindCite      Kind = "cite"
)

func Classify(q string) Kind {
	s := strings.ToLower(q)
	switch {
	case strings.Contains(s, "原文") || (strings.Contains(s, "第") && strings.Contains(s, "条")):
		return KindCite
	case strings.Contains(s, "现在") || strings.Contains(s, "还在") || strings.Contains(s, "升级"):
		return KindTemporal
	default:
		return KindStructure
	}
}
