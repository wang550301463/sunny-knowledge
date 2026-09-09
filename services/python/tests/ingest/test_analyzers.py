from dataclasses import asdict
from pathlib import Path

import pytest

import knowledge_platform.ingest.analyzers as analyzers

FIXTURES = Path(__file__).parent / "fixtures" / "monorepo"


def analyze(*sources, repo_id="example/monorepo", revision="abc123"):
    return analyzers.analyze_snapshot(analyzers.SourceSnapshot(repo_id, revision, tuple(sources)))


def fixture(path):
    return analyzers.SourceFile(path, (FIXTURES / path).read_bytes())


def test_public_snapshot_contract_is_available():
    assert hasattr(analyzers, "SourceFile")
    assert hasattr(analyzers, "SourceSnapshot")
    assert hasattr(analyzers, "AnalyzerRegistry")
    assert hasattr(analyzers, "analyze_snapshot")


def test_java_multiline_overloads_nested_declarations_and_import_locations():
    result = analyze(fixture("api/src/main/java/example/Service.java"))
    file = result.files[0]
    assert file.language == "java"
    assert file.entity_type == "File"
    assert file.package == "example"
    assert [i.module for i in file.imports] == ["java.util.List", "java.util.Collections.emptyList"]
    assert file.imports[1].location.start_line == 4
    assert file.imports[1].is_static
    assert {s.name for s in file.symbols} == {"Service", "find", "Nested", "run"}
    finds = [s for s in file.symbols if s.name == "find"]
    assert len(finds) == 2
    assert len({s.id for s in finds}) == 2
    assert finds[0].location.start_line == 9
    assert finds[0].location.end_line == 14
    assert next(s for s in file.symbols if s.name == "run").qualified_name == "example.Service.Nested.run"
    assert all(s.evidence == "ast" for s in file.symbols)
    assert result.diagnostics == ()


def test_go_methods_receiver_and_exact_multiline_locations():
    result = analyze(fixture("worker/service.go"))
    file = result.files[0]
    assert file.package == "worker"
    assert [i.module for i in file.imports] == ["context", "example.com/storage/client"]
    assert file.imports[1].alias == "db"
    method = next(s for s in file.symbols if s.name == "Find")
    assert method.kind == "method"
    assert method.qualified_name == "worker.Service.Find"
    assert method.location.start_line == 10
    assert method.location.end_line == 15
    assert "query string" in method.signature
    assert {s.name for s in file.symbols} == {"Service", "Find", "New"}


def test_typescript_ast_handles_type_import_reexport_methods_arrow_and_tsx():
    result = analyze(fixture("web/src/service.ts"), analyzers.SourceFile("web/src/View.tsx", "export const View = () => <span>你好</span>;"))
    file = next(f for f in result.files if f.path.endswith("service.ts"))
    assert [i.module for i in file.imports] == ["@example/client", "./polyfill", "./helper"]
    assert file.imports[0].location.start_line == 1
    assert file.imports[0].location.end_line == 3
    assert file.imports[0].is_type_only
    assert file.imports[2].kind == "reexport"
    assert next(s for s in file.symbols if s.name == "load").location.start_line == 17
    assert {s.name for s in file.symbols} == {"Repository", "Service", "find", "load"}
    assert result.diagnostics == ()
    assert result.files[0].symbols[0].name == "View"


def test_ids_are_namespaced_and_revision_independent_and_json_serializable():
    first = analyze(analyzers.SourceFile("a/Service.java", "class Service {}"), analyzers.SourceFile("b/Service.java", "class Service {}"))
    next_revision = analyze(analyzers.SourceFile("a/Service.java", "\nclass Service {}"), revision="def456")
    another_repo = analyze(analyzers.SourceFile("a/Service.java", "class Service {}"), repo_id="another/repo")
    assert len({f.id for f in first.files}) == 2
    assert len({m.id for m in first.modules}) == 2
    assert first.files[0].id == next_revision.files[0].id
    assert first.files[0].symbols[0].id == next_revision.files[0].symbols[0].id
    assert first.files[0].id != another_repo.files[0].id
    assert asdict(first)["source_revision"] == "abc123"


@pytest.mark.parametrize("path", ["/etc/passwd", "../escape.go", "a/../b.go", "C:/file.go", "a\\b.go", "a//b.go", "./a.go", "a/\x00.go"])
def test_unsafe_snapshot_paths_are_rejected_without_reading_them(path):
    result = analyze(analyzers.SourceFile(path, "package p"))
    assert result.files == ()
    assert result.diagnostics[0].code == "invalid_path"


def test_invalid_utf8_binary_and_symlinks_are_explicitly_skipped():
    result = analyze(analyzers.SourceFile("invalid.go", b"\xff"), analyzers.SourceFile("binary.go", b"a\x00b"), analyzers.SourceFile("linked.go", "package p", kind="symlink"))
    assert result.files == ()
    assert {d.code for d in result.diagnostics} == {"invalid_utf8", "binary_file", "symlink_skipped"}


def test_unknown_file_duplicate_paths_and_invalid_snapshots_are_explicit():
    result = analyze(analyzers.SourceFile("x.rs", "fn hi() {}"), analyzers.SourceFile("same.go", "package p"), analyzers.SourceFile("same.go", "package q"))
    assert {d.code for d in result.diagnostics} == {"unsupported_file", "duplicate_path"}
    assert len(result.files) == 1
    with pytest.raises(ValueError):
        analyzers.SourceSnapshot("", "rev", ())
    with pytest.raises(ValueError):
        analyzers.SourceSnapshot("repo", "", ())


@pytest.mark.parametrize("path,source", [("Broken.java", "class Broken { void fabricated( { }"), ("broken.ts", "export function fabricated( {"), ("broken.go", "package broken\nfunc fabricated( {")])
def test_malformed_source_does_not_fabricate_methods(path, source):
    result = analyze(analyzers.SourceFile(path, source))
    assert any(d.code == "syntax_error" and d.location.start_line >= 1 for d in result.diagnostics)
    assert not any(s.name == "fabricated" for s in result.files[0].symbols)


def test_registry_can_add_a_new_file_kind_without_changing_snapshot_pipeline():
    class TextAnalyzer:
        name = "text-test"
        def supports(self, path):
            return path.endswith(".notes")
        def analyze(self, context):
            return analyzers.AnalyzerOutput(diagnostics=(analyzers.Diagnostic("custom", "Handled by extension", context.location(0, len(context.data))),))
    registry = analyzers.AnalyzerRegistry((TextAnalyzer(),))
    result = analyzers.analyze_snapshot(analyzers.SourceSnapshot("repo", "rev", (analyzers.SourceFile("a.notes", "abc"),)), registry)
    assert result.diagnostics[0].code == "custom"