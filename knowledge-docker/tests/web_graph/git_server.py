"""Separate read-only Git fixture: two actual declared dependencies form a two-hop path."""

import importlib.util
import os
import tempfile
from pathlib import Path


def prepare(root: Path):
    path = Path(os.environ.get("WEB_GRAPH_BASE_GIT_SERVER", Path(__file__).resolve().parents[1] / "git_fixture/server.py"))
    spec = importlib.util.spec_from_file_location("web_graph_base_git_server", path)
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    # This separate process owns only its temporary fixture repository. Production
    # code, the original fixture container and source declarations are not altered.
    extra = """    <dependency>
      <groupId>com.google.guava</groupId>
      <artifactId>guava</artifactId>
      <version>33.4.0-jre</version>
    </dependency>
"""
    backend.FILES["java/pom.xml"] = backend.FILES["java/pom.xml"].replace("  </dependencies>", extra + "  </dependencies>")
    backend.CHANGES["java/pom.xml"] = backend.FILES["java/pom.xml"].replace("2.0.16", "2.0.17")
    manifest = backend.build_repository(root)
    manifest["kind"] = "web_graph_three_language_git_fixture"
    manifest["dependencies"] = ["org.slf4j:slf4j-api", "com.google.guava:guava"]
    return manifest, backend


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="knowledge-web-graph-git-") as directory:
        root = Path(directory) / "repositories"
        manifest, backend = prepare(root)
        backend.make_server(root, manifest).serve_forever()