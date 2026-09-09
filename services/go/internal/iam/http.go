package iam
import("net/http";"github.com/wang550301463/sunny-knowledge/services/go/internal/platform")
func NewHandler(s *Store,c *platform.Client,authURL string,sec *platform.ServiceSecurity)http.Handler{return http.NotFoundHandler()}
