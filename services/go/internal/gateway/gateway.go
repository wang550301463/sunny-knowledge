package gateway
import("net/http";"github.com/wang550301463/sunny-knowledge/services/go/internal/platform")
type Config struct{AuthURL,KeycloakURL,WebURL string;Routes map[string]string}
func NewHandler(c Config,s *platform.ServiceSecurity)(http.Handler,error){return http.NotFoundHandler(),nil}
