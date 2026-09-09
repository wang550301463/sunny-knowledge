package platform

import (
	"bufio"
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

// Deliberately avoid otelhttp defaults: raw URL/path, IDs, payloads, headers and
// exception strings must never become telemetry attributes or metric labels.
type Telemetry struct {
	Registry  *prometheus.Registry
	tracer    trace.Tracer
	requests  *prometheus.CounterVec
	duration  *prometheus.HistogramVec
	cancelled *prometheus.CounterVec
	service   string
}
type telemetryKey struct{}
type targetKey struct{}

var tracePropagator = propagation.TraceContext{}

func safeService(s string) string {
	switch s {
	case "gateway", "iam", "auth", "channel", "channel-worker", "knowledge", "ingest", "retrieval", "llm", "graphiti", "agent", "mcp", "web", "keycloak":
		return s
	}
	return "external"
}
func safeMethod(s string) string {
	switch s {
	case "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS":
		return s
	}
	return "OTHER"
}

// Coarse fixed templates are intentionally bounded even on gateway passthrough
// and middleware-auth failures, before a nested ServeMux chooses its handler.
func SafeRoute(path string) string {
	for _, prefix := range []string{"/api/v1/", "/internal/v1/"} {
		if strings.HasPrefix(path, prefix) {
			parts := strings.Split(strings.TrimPrefix(path, prefix), "/")
			name := parts[0]
			allowed := false
			if prefix == "/api/v1/" {
				switch name {
				case "me", "spaces", "groups", "departments", "users", "service-accounts", "grants", "audit", "pages", "source-snapshots", "reviews", "revisions", "sources", "tasks", "search", "traverse", "timeline", "models", "agents", "sessions", "runs", "feedback", "channels", "channel-bindings", "bindings":
					allowed = true
				}
			}
			if prefix == "/internal/v1/" {
				switch name {
				case "resolve", "authorize", "authorize-batch", "principals", "check", "check-channel", "channel-audiences", "policies", "resources", "source-resources", "channel-token", "channel-run-token", "contexts", "runs", "channel", "source-fences", "projections", "sources", "models", "search", "traverse", "timeline", "pages", "reviews", "revisions", "source-snapshots":
					allowed = true
				}
			}
			if !allowed {
				return "unmatched"
			}
			if len(parts) > 1 {
				return prefix + name + "/{path...}"
			}
			return prefix + name
		}
	}
	switch {
	case path == "/healthz" || path == "/readyz":
		return path
	case path == "/mcp" || strings.HasPrefix(path, "/mcp/"):
		return "/mcp/{path...}"
	case path == "/idp" || strings.HasPrefix(path, "/idp/"):
		return "/idp/{path...}"
	case strings.HasPrefix(path, "/.well-known/"):
		return "/.well-known/{path...}"
	case path == "/" || strings.HasPrefix(path, "/assets/"):
		return "web"
	}
	return "unmatched"
}
func NewTelemetry(service string, provider trace.TracerProvider) *Telemetry {
	if provider == nil {
		provider = otel.GetTracerProvider()
	}
	labels := []string{"service", "method", "route", "status"}
	t := &Telemetry{Registry: prometheus.NewRegistry(), tracer: provider.Tracer("knowledge-platform.http"), service: safeService(service)}
	t.requests = prometheus.NewCounterVec(prometheus.CounterOpts{Name: "knowledge_http_requests_total", Help: "Completed HTTP requests"}, labels)
	t.duration = prometheus.NewHistogramVec(prometheus.HistogramOpts{Name: "knowledge_http_request_duration_seconds", Help: "HTTP duration including streamed response completion", Buckets: []float64{.01, .025, .05, .1, .25, .5, 1, 2, 5, 10, 30, 60, 180}}, labels)
	t.cancelled = prometheus.NewCounterVec(prometheus.CounterOpts{Name: "knowledge_http_cancelled_total", Help: "HTTP requests whose context was cancelled"}, []string{"service", "method", "route"})
	t.Registry.MustRegister(t.requests, t.duration, t.cancelled)
	return t
}
func traceFor(ctx context.Context) trace.Tracer {
	if t, ok := ctx.Value(telemetryKey{}).(*Telemetry); ok {
		return t.tracer
	}
	return otel.Tracer("knowledge-platform.http")
}
func (t *Telemetry) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/healthz" || r.URL.Path == "/readyz" {
			next.ServeHTTP(w, r)
			return
		}
		method, route := safeMethod(r.Method), SafeRoute(r.URL.Path)
		// W3C traceparent is the only inbound carrier. Baggage/tracestate are not accepted.
		ctx := tracePropagator.Extract(r.Context(), propagation.MapCarrier{"traceparent": r.Header.Get("Traceparent")})
		ctx, span := t.tracer.Start(ctx, method+" "+route, trace.WithSpanKind(trace.SpanKindServer))
		r = r.WithContext(context.WithValue(ctx, telemetryKey{}, t))
		observed := &observedWriter{ResponseWriter: w}
		started := time.Now()
		complete := false
		defer func() {
			status := observed.status
			if !complete {
				status = 500
			}
			if status == 0 {
				status = 200
			}
			span.SetAttributes(attribute.String("http.request.method", method), attribute.String("http.route", route), attribute.Int("http.response.status_code", status))
			if status >= 500 || r.Context().Err() != nil {
				span.SetStatus(codes.Error, "")
			}
			if r.Context().Err() != nil {
				t.cancelled.WithLabelValues(t.service, method, route).Inc()
			}
			t.requests.WithLabelValues(t.service, method, route, strconv.Itoa(status)).Inc()
			t.duration.WithLabelValues(t.service, method, route, strconv.Itoa(status)).Observe(time.Since(started).Seconds())
			span.End()
		}()
		next.ServeHTTP(observed, r)
		complete = true
	})
}

type observedWriter struct {
	http.ResponseWriter
	status int
}

func (w *observedWriter) WriteHeader(code int) {
	if code >= 100 && code < 200 {
		w.ResponseWriter.WriteHeader(code)
		return
	}
	if w.status == 0 {
		w.status = code
		w.ResponseWriter.WriteHeader(code)
	}
}
func (w *observedWriter) Write(b []byte) (int, error) {
	if w.status == 0 {
		w.WriteHeader(200)
	}
	return w.ResponseWriter.Write(b)
}
func (w *observedWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }
func (w *observedWriter) Flush() {
	if w.status == 0 {
		w.WriteHeader(200)
	}
	_ = http.NewResponseController(w.ResponseWriter).Flush()
}
func (w *observedWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	return http.NewResponseController(w.ResponseWriter).Hijack()
}

// TraceTransport creates only allowlisted attributes and ends the span on body
// EOF/close/cancellation, rather than at receipt of response headers (SSE).
type TraceTransport struct {
	Base   http.RoundTripper
	Target string
}

func (t TraceTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	base := t.Base
	if base == nil {
		base = http.DefaultTransport
	}
	target := t.Target
	if target == "" {
		target, _ = r.Context().Value(targetKey{}).(string)
	}
	method, route := safeMethod(r.Method), SafeRoute(r.URL.Path)
	ctx, span := traceFor(r.Context()).Start(r.Context(), method+" "+route, trace.WithSpanKind(trace.SpanKindClient), trace.WithAttributes(attribute.String("http.request.method", method), attribute.String("http.route", route), attribute.String("peer.service", safeService(target))))
	out := r.Clone(ctx)
	out.Header.Del("Baggage")
	out.Header.Del("Tracestate")
	out.Header.Del("Traceparent")
	tracePropagator.Inject(ctx, propagation.HeaderCarrier(out.Header))
	out.Header.Del("Tracestate")
	response, err := base.RoundTrip(out)
	if err != nil {
		span.SetStatus(codes.Error, "")
		span.End()
		return nil, err
	}
	span.SetAttributes(attribute.Int("http.response.status_code", response.StatusCode))
	if response.StatusCode >= 500 {
		span.SetStatus(codes.Error, "")
	}
	var once sync.Once
	finish := func(err error) {
		once.Do(func() {
			if (err != nil && err != io.EOF) || ctx.Err() != nil {
				span.SetStatus(codes.Error, "")
			}
			span.End()
		})
	}
	stop := context.AfterFunc(ctx, func() { finish(ctx.Err()) })
	response.Body = &traceBody{ReadCloser: response.Body, finish: finish, stop: stop}
	return response, nil
}

type traceBody struct {
	io.ReadCloser
	finish func(error)
	stop   func() bool
}

func (b *traceBody) Read(v []byte) (int, error) {
	n, e := b.ReadCloser.Read(v)
	if e != nil {
		b.stop()
		b.finish(e)
	}
	return n, e
}
func (b *traceBody) Close() error { e := b.ReadCloser.Close(); b.stop(); b.finish(e); return e }

// Each authenticated channel callback gets a root span. Message/user/bot/run IDs
// and content are intentionally unavailable to this helper.
func StartChannelMessage(ctx context.Context) (context.Context, trace.Span) {
	return traceFor(ctx).Start(ctx, "channel.message", trace.WithNewRoot(), trace.WithSpanKind(trace.SpanKindConsumer))
}

type TelemetryRuntime struct {
	Telemetry *Telemetry
	provider  *sdktrace.TracerProvider
	server    *http.Server
	listener  net.Listener
}

func StartTelemetryFromEnv(service string) (*TelemetryRuntime, error) {
	port := 0
	var e error
	if raw := os.Getenv("METRICS_PORT"); raw != "" {
		port, e = strconv.Atoi(raw)
		if e != nil || port < 0 || port > 65535 {
			return nil, errors.New("invalid metrics port")
		}
	}
	options := []sdktrace.TracerProviderOption{sdktrace.WithResource(resource.NewWithAttributes("", attribute.String("service.name", safeService(service)), attribute.String("service.version", "2.0.0"))), sdktrace.WithSampler(sdktrace.ParentBased(sdktrace.AlwaysSample()))}
	if endpoint := os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT"); endpoint != "" {
		u, err := url.Parse(endpoint)
		if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") || (u.Scheme != "http" && u.Scheme != "https") {
			return nil, errors.New("invalid telemetry endpoint")
		}
		exporter, err := otlptracehttp.New(context.Background(), otlptracehttp.WithEndpointURL(strings.TrimRight(endpoint, "/")+"/v1/traces"), otlptracehttp.WithTimeout(3*time.Second), otlptracehttp.WithRetry(otlptracehttp.RetryConfig{Enabled: false}))
		if err != nil {
			return nil, errors.New("telemetry exporter unavailable")
		}
		options = append(options, sdktrace.WithBatcher(exporter, sdktrace.WithMaxQueueSize(2048), sdktrace.WithMaxExportBatchSize(256), sdktrace.WithBatchTimeout(time.Second), sdktrace.WithExportTimeout(3*time.Second)))
	}
	provider := sdktrace.NewTracerProvider(options...)
	runtime := &TelemetryRuntime{provider: provider, Telemetry: NewTelemetry(service, provider)}
	if port > 0 {
		listener, err := net.Listen("tcp", ":"+strconv.Itoa(port))
		if err != nil {
			provider.Shutdown(context.Background())
			return nil, errors.New("metrics listener unavailable")
		}
		runtime.listener = listener
		mux := http.NewServeMux()
		mux.Handle("GET /metrics", promhttp.HandlerFor(runtime.Telemetry.Registry, promhttp.HandlerOpts{MaxRequestsInFlight: 4, Timeout: 3 * time.Second}))
		runtime.server = &http.Server{Handler: mux, ReadHeaderTimeout: 3 * time.Second, ReadTimeout: 5 * time.Second, WriteTimeout: 5 * time.Second, IdleTimeout: 15 * time.Second, MaxHeaderBytes: 8 << 10}
		go func() { _ = runtime.server.Serve(listener) }()
	}
	otel.SetTracerProvider(provider)
	// Export errors are operational counts, never exporter response/exception text.
	exportErrors := prometheus.NewCounter(prometheus.CounterOpts{Name: "knowledge_telemetry_export_errors_total", Help: "Telemetry export failures"})
	runtime.Telemetry.Registry.MustRegister(exportErrors)
	otel.SetErrorHandler(otel.ErrorHandlerFunc(func(error) { exportErrors.Inc() }))
	return runtime, nil
}
func (r *TelemetryRuntime) Close() {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if r.server != nil {
		_ = r.server.Shutdown(ctx)
	}
	_ = r.provider.Shutdown(ctx)
}
