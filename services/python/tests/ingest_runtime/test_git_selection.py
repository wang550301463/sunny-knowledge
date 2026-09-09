"""Real Git path selection and bounded batch reads for large repositories."""

import pytest

from knowledge_platform.ingest.connectors import GitConnector, SnapshotLimits
from knowledge_platform.ingest.schemas import SourceCreate

from .test_git import git, smart_http


@pytest.fixture
def selected_repository(tmp_path):
    work = tmp_path / "work"
    server = tmp_path / "server"
    work.mkdir()
    server.mkdir()
    git(work, "init", "-q")
    content = {
        "main.go": b"package main\r\n",
        "src/pay/main.go": b"package pay\n",
        "src/pay/test/main.go": b"package test\n",
        "src/ui/main.ts": "export const label = '支付';\n".encode(),
        "vendor/lib/main.go": b"package vendored\n",
        "docs/readme.md": b"# Source\n",
        "empty.go": b"",
        "build/archive.bin": b"x" * 5000,
    }
    for path, data in content.items():
        target = work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    git(work, "add", ".")
    git(work, "commit", "-qm", "selected files")
    git(server, "clone", "--bare", str(work), "repo.git")
    return server, content


def test_selected_files_keep_exact_originals_and_exclude_before_content_budget(
    selected_repository, monkeypatch
):
    server, content = selected_repository
    connector = GitConnector(SnapshotLimits(max_file_bytes=100, max_bytes=500))
    calls = []
    original = connector._run

    def traced(args, *positional, **kwargs):
        calls.append(args)
        return original(args, *positional, **kwargs)

    monkeypatch.setattr(connector, "_run", traced)
    with smart_http(server) as url:
        result = connector.capture(
            {
                "url": url,
                "ref": "HEAD",
                "include_paths": ["**/*.go", "src/**/*.ts"],
                "exclude_paths": ["vendor/**", "**/test/**"],
            },
            None,
            1,
        )
    expected = {"main.go", "empty.go", "src/pay/main.go", "src/ui/main.ts"}
    assert {file.path: file.data for file in result.files} == {
        path: content[path] for path in expected
    }
    assert "path_filter_excluded:4" in result.diagnostics
    # Reading a selected repository must not launch one process per blob.
    cat_calls = [args for args in calls if "cat-file" in args]
    assert len(cat_calls) == 1
    assert "--batch" in cat_calls[0]


def test_single_segment_wildcard_does_not_match_nested_paths(selected_repository):
    server, _ = selected_repository
    with smart_http(server) as url:
        result = GitConnector().capture(
            {"url": url, "ref": "HEAD", "include_paths": ["src/*/main.go"]}, None, 1
        )
    assert [file.path for file in result.files] == ["src/pay/main.go"]


def test_empty_selection_is_explicit_and_does_not_read_excluded_large_blobs(
    selected_repository, monkeypatch
):
    server, _ = selected_repository
    connector = GitConnector(SnapshotLimits(max_file_bytes=1))
    original = connector._run

    def no_blob_reads(args, *positional, **kwargs):
        assert "cat-file" not in args
        return original(args, *positional, **kwargs)

    monkeypatch.setattr(connector, "_run", no_blob_reads)
    with smart_http(server) as url:
        result = connector.capture(
            {"url": url, "ref": "HEAD", "include_paths": ["missing/**"]}, None, 1
        )
    assert result.files == ()
    assert "path_filter_excluded:8" in result.diagnostics


@pytest.mark.parametrize(
    "filters",
    [
        {"include_paths": "**/*.go"},
        {"exclude_paths": ["../private/**"]},
        {"include_paths": ["/root/**"]},
        {"include_paths": ["src/**suffix"]},
        {"include_paths": ["src/[ab]/*.go"]},
        {"include_paths": ["src\\main.go"]},
        {"exclude_paths": [None]},
        {"include_paths": ["**"] * 101},
    ],
)
def test_invalid_path_filters_are_rejected_before_network_capture(filters):
    with pytest.raises(ValueError):
        SourceCreate(
            name="Source",
            space_id="space",
            kind="git",
            config={"url": "https://git.example/repo.git", "ref": "HEAD", **filters},
        )
