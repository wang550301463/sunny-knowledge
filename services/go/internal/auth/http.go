package auth
import("net/http";"github.com/wang550301463/sunny-knowledge/services/go/internal/platform")
func NewHandler(v *Verifier,c *platform.Client,iamURL string,sec *platform.ServiceSecurity)http.Handler{return http.NotFoundHandler()}
