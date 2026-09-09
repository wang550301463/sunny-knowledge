"""Dispatch only provided, valid text snapshots; never access a repository path."""
from __future__ import annotations

from .models import AnalysisContext, AnalysisResult, Analyzer, Diagnostic, SourceSnapshot, safe_path
from .source import TreeSitterAnalyzer


class AnalyzerRegistry:
    def __init__(self, analyzers: tuple[Analyzer, ...] | None = None):
        self._analyzers = list(analyzers if analyzers is not None else (TreeSitterAnalyzer(),))

    def register(self, analyzer: Analyzer) -> None:
        if any(existing.name == analyzer.name for existing in self._analyzers):
            raise ValueError(f"Analyzer already registered: {analyzer.name}")
        self._analyzers.append(analyzer)

    def resolve(self, path: str) -> Analyzer | None:
        return next((analyzer for analyzer in self._analyzers if analyzer.supports(path)), None)


def analyze_snapshot(snapshot: SourceSnapshot, registry: AnalyzerRegistry | None = None) -> AnalysisResult:
    registry = registry or AnalyzerRegistry()
    collected = {key: [] for key in ("files", "modules", "dependencies", "relations", "diagnostics")}
    diagnostics = collected["diagnostics"]
    seen = set()
    for source in sorted(snapshot.files, key=lambda f: f.path):
        def issue(code, message):
            diagnostics.append(Diagnostic(code, message, severity="error" if code in {"invalid_path", "duplicate_path", "invalid_utf8"} else "warning", path=source.path))
        if not safe_path(source.path):
            issue("invalid_path", "Expected a canonical relative POSIX path without traversal")
            continue
        if source.path in seen:
            issue("duplicate_path", "Duplicate path ignored; snapshots must contain unique paths")
            continue
        seen.add(source.path)
        if source.kind != "regular":
            issue("symlink_skipped" if source.kind == "symlink" else "unsupported_file_kind", "Only regular snapshot files may be analyzed")
            continue
        try:
            data = source.content.encode("utf-8") if isinstance(source.content, str) else source.content
            data.decode("utf-8", errors="strict")
        except UnicodeError:
            issue("invalid_utf8", "Source is not valid UTF-8; no replacement characters were inserted")
            continue
        if any(c < 32 and c not in (9, 10, 12, 13) for c in data):
            issue("binary_file", "Binary/control bytes found; source skipped")
            continue
        analyzer = registry.resolve(source.path)
        if analyzer is None:
            issue("unsupported_file", "No analyzer registered for this file kind")
            continue
        output = analyzer.analyze(AnalysisContext(snapshot.repo_id, snapshot.source_revision, source.path, data))
        for key, values in collected.items():
            values.extend(getattr(output, key))
    collected["modules"] = list({module.id: module for module in collected["modules"]}.values())
    return AnalysisResult(**{key: tuple(values) for key, values in collected.items()}, repo_id=snapshot.repo_id, source_revision=snapshot.source_revision)