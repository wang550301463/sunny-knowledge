# Immutable snapshot analyzers

This package accepts only caller-provided text/bytes. It does not traverse directories,
follow links, access network resources, import repository modules, invoke subprocesses,
or run package managers/build tools. The connector must enumerate the immutable source
snapshot, mark symlinks, enforce snapshot/file size budgets and retain raw bytes.

```python
from dataclasses import asdict
from knowledge_platform.ingest.analyzers import SourceFile, SourceSnapshot, analyze_snapshot

result = analyze_snapshot(SourceSnapshot(
    repo_id="example/platform",
    source_revision="full-source-revision",
    files=(SourceFile("src/main.go", b"package main\nfunc main() {}\n"),),
))
wire_payload = asdict(result)
```

`AnalysisResult` contains `files`, `modules`, `dependencies`, `relations`, `diagnostics`,
`repo_id`, `source_revision`, and `analyzer_version`. Public dataclasses are exported from
`__init__.py`; all nested fields serialize with `dataclasses.asdict` and standard JSON.
Source file IDs use the repository namespace and complete relative path. Module IDs use
the repository, module directory and language. Symbols remain structured `File` details;
there is no new schema entity or generated `Procedure`. Callable IDs include normalized
headers to distinguish overloads. The revision is pinned on the result, not embedded in
entity IDs. Header changes can change callable identity; byte/line shifts alone do not.

Locations retain exact byte spans. Start lines/columns are 1-based; bytes are zero-based
and end-exclusive. Columns count UTF-8 bytes, not display characters. `end_line` is the
last line containing span bytes; `end_column` describes the exclusive endpoint (which can
be column 1 on the next line when the span ends with a newline). Consumers producing
snippets should use byte offsets or inclusive line ranges, not mix those two conventions.

`Analyzer` is a protocol with `name`, `supports(path)` and `analyze(AnalysisContext)`.
`AnalyzerRegistry()` installs the built-ins. Pass an explicit tuple to replace them or
call `register` to add a file kind. Dispatch is first match, and duplicate names are
rejected. Custom analyzers receive validated text bytes and no filesystem capability.
The protocol is an extension seam, not a sandbox for malicious installed plugins.

## Evidence and conservative limits

- Tree-sitter parses Java, Go, TypeScript, TSX, MTS and CTS using actual grammars. It emits
  declarations, common fields/constants/variables and static imports with exact spans.
  Grammar ERROR/MISSING nodes produce diagnostics. A declaration containing a syntax
  error is not emitted; healthy sibling declarations can still be retained. There is no
  type checker, macro/build-tag evaluation, overload resolution or complete call graph.
- TypeScript `require(...)` calls are marked `inferred`, since `require` may be shadowed.
  Dynamic import targets are diagnosed. Import aliases and wildcard resolution are not
  a symbol binding analysis. Destructuring and computed property names are not flattened
  into invented symbols. Source files in unsupported languages receive diagnostics.
- Build modules take priority over directory/package modules. Assigning a source file to
  the nearest manifest creates a `uses` relation marked `inferred`; it is not proof that
  a build includes the file. Explicit direct dependency declarations create `depends_on`
  relations with manifest evidence; neither proves a deployed production version.
- `go.mod` provides minimum required versions. Replacements become explicit unresolved
  selection constraints. Exclude/retract/tool directives are diagnosed as unresolved.
  `go.sum` is checksum-only evidence, **not** a selected dependency graph or lockfile.
  Go workspace selection and module graph resolution are not executed.
- npm package manifests and lockfile v1/v2/v3 are parsed, including nested install paths.
  Declared and resolved records remain separate evidence. A matching root lock request
  can enrich the manifest record with a resolved version and exact resolution path/lines
  in metadata. A mismatched root request produces a diagnostic and no version merge.
  Workspace links and missing locked versions are unresolved. npm-shrinkwrap is recognized;
  if both it and package-lock are provided, the connector should select the authoritative
  one before analysis. No semver solving, registry lookup, pnpm or Yarn parsing occurs.
- Maven POM XML namespaces and local properties are supported. Declared/interpolated
  versions remain manifest facts, never claimed as resolved versions. Parent/BOM imports,
  profile activation and plugin behavior are not evaluated. Management declarations are
  distinguished from direct dependencies. DTDs/external entities are rejected.
- Gradle is an explicitly **lexical static adapter**, not a Kotlin/Groovy AST or evaluator.
  It supports direct literal coordinates and the standard ordered group/name/version map
  notation. Nested/conditional blocks, interpolation, catalogs and other expressions stay
  unresolved. Its diagnostics always disclose the static-only scope; no build executes.
- Invalid paths, duplicate paths, symlinks, binary/control bytes and invalid UTF-8 are
  diagnosed. The first duplicate path is retained with an error; a connector should reject
  duplicate snapshots rather than silently publish partial results. Error diagnostics must
  be considered by the compiler/publication policy; successful parsing is not compilation.

## Verified runtime and references

Tested with CPython 3.12.12, tree-sitter 0.25.2, tree-sitter-java 0.23.5,
tree-sitter-typescript 0.23.2 and tree-sitter-go 0.25.0. The parent package pins these.

- [Official Python Tree-sitter API and usage](https://pypi.org/project/tree-sitter/0.25.2/)
- [Java grammar package](https://pypi.org/project/tree-sitter-java/0.23.5/)
- [TypeScript grammar package](https://pypi.org/project/tree-sitter-typescript/0.23.2/)
- [Go grammar package](https://pypi.org/project/tree-sitter-go/0.25.0/)
- [Go modules reference](https://go.dev/ref/mod)
- [npm lockfile format](https://docs.npmjs.com/cli/v11/configuring-npm/package-lock-json/)
- [Maven POM reference](https://maven.apache.org/pom.html)
- [Gradle dependency declarations](https://docs.gradle.org/current/userguide/declaring_dependencies.html)

Run from the repository root:

```sh
PYTHONPATH=services/python/src services/python/.venv/bin/python -m pytest services/python/tests/ingest -q
services/python/.venv/bin/python -m ruff check services/python/src/knowledge_platform/ingest/analyzers services/python/tests/ingest
```