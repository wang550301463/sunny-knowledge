"""Pure snapshot contracts. Locations use 1-based lines and UTF-8 byte columns.

End byte/column are exclusive; end_line is the last line containing source bytes.
Symbols are File details, not a new business entity type.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import PurePosixPath
from typing import Literal, Protocol
from urllib.parse import quote

Evidence = Literal["ast", "manifest", "lockfile", "inferred"]


def stable_id(repo_id: str, kind: str, *parts: str) -> str:
    digest = sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:32]
    return f"source:{quote(repo_id, safe='')}:{kind}:{digest}"


def safe_path(path: str) -> bool:
    return bool(path) and not any(ord(c) < 32 for c in path) and "\\" not in path and ":" not in path and all(p not in ("", ".", "..") for p in path.split("/"))


@dataclass(frozen=True)
class SourceFile:
    path: str
    content: str | bytes
    kind: Literal["regular", "symlink"] = "regular"


@dataclass(frozen=True)
class SourceSnapshot:
    repo_id: str
    source_revision: str
    files: tuple[SourceFile, ...]

    def __post_init__(self):
        if not self.repo_id.strip() or not self.source_revision.strip():
            raise ValueError("repo_id and source_revision are required")
        object.__setattr__(self, "files", tuple(self.files))


@dataclass(frozen=True)
class Location:
    path: str
    start_line: int
    end_line: int
    start_column: int
    end_column: int
    start_byte: int
    end_byte: int


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    location: Location | None = None
    severity: Literal["info", "warning", "error"] = "warning"
    path: str | None = None


@dataclass(frozen=True)
class SymbolFact:
    id: str
    name: str
    qualified_name: str
    kind: str
    signature: str
    location: Location
    evidence: Evidence = "ast"


@dataclass(frozen=True)
class ImportFact:
    module: str
    location: Location
    alias: str | None = None
    kind: str = "import"
    is_static: bool = False
    is_type_only: bool = False
    evidence: Evidence = "ast"


@dataclass(frozen=True)
class FileFact:
    id: str
    path: str
    language: str
    content_hash: str
    location: Location
    module_id: str | None = None
    package: str | None = None
    symbols: tuple[SymbolFact, ...] = ()
    imports: tuple[ImportFact, ...] = ()
    entity_type: Literal["File"] = "File"
    evidence: Evidence = "ast"


@dataclass(frozen=True)
class ModuleFact:
    id: str
    name: str
    path: str
    language: str
    location: Location
    evidence: Evidence
    version: str | None = None
    entity_type: Literal["Module"] = "Module"


@dataclass(frozen=True)
class DependencyFact:
    id: str
    module_id: str
    name: str
    ecosystem: str
    location: Location
    declared_version: str | None = None
    resolved_version: str | None = None
    scope: str = "runtime"
    direct: bool | None = True
    resolution: Literal["declared", "resolved", "unresolved", "checksum_only"] = "declared"
    evidence: Evidence = "manifest"
    reason: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    entity_type: Literal["Dependency"] = "Dependency"


@dataclass(frozen=True)
class RelationFact:
    source_id: str
    target_id: str
    kind: Literal["uses", "depends_on"]
    location: Location
    evidence: Evidence
    reason: str


@dataclass(frozen=True)
class AnalyzerOutput:
    files: tuple[FileFact, ...] = ()
    modules: tuple[ModuleFact, ...] = ()
    dependencies: tuple[DependencyFact, ...] = ()
    relations: tuple[RelationFact, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class AnalysisResult(AnalyzerOutput):
    repo_id: str = ""
    source_revision: str = ""
    analyzer_version: str = "tree-sitter-static-v1"


@dataclass(frozen=True)
class AnalysisContext:
    repo_id: str
    source_revision: str
    path: str
    data: bytes
    _line_starts: tuple[int, ...] = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "_line_starts", (0,) + tuple(i + 1 for i, c in enumerate(self.data) if c == 10))

    @property
    def text(self) -> str:
        return self.data.decode("utf-8")

    @property
    def directory(self) -> str:
        return str(PurePosixPath(self.path).parent)

    def location(self, start: int, end: int) -> Location:
        start_line = bisect_right(self._line_starts, start)
        end_line = bisect_right(self._line_starts, max(start, end - 1))
        end_point_line = bisect_right(self._line_starts, end)
        return Location(self.path, start_line, end_line, start - self._line_starts[start_line - 1] + 1, end - self._line_starts[end_point_line - 1] + 1, start, end)

    def module(self, name: str, language: str, evidence: Evidence, location: Location | None = None, version: str | None = None) -> ModuleFact:
        return ModuleFact(stable_id(self.repo_id, "module", self.directory, language), name, self.directory, language, location or self.location(0, len(self.data)), evidence, version)

    def file(self, language: str, **kwargs) -> FileFact:
        return FileFact(stable_id(self.repo_id, "file", self.path), self.path, language, sha256(self.data).hexdigest(), self.location(0, len(self.data)), **kwargs)


class Analyzer(Protocol):
    name: str

    def supports(self, path: str) -> bool: ...

    def analyze(self, context: AnalysisContext) -> AnalyzerOutput: ...