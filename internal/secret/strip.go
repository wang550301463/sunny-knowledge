package secret

import "regexp"

var patterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)(ghp|gho|github_pat)_[A-Za-z0-9_]{10,}`),
	regexp.MustCompile(`(?i)sk-[A-Za-z0-9]{10,}`),
	regexp.MustCompile(`(?i)(password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+`),
}

func Strip(s string) string {
	out := s
	for _, re := range patterns {
		out = re.ReplaceAllString(out, "[REDACTED]")
	}
	return out
}
