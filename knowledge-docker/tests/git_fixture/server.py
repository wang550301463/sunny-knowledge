"""Isolated read-only smart HTTP fixture. Never expose this test server publicly."""
import hashlib
import json
import os
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

FILES = {
    "README.md": "# Fixture release procedure\nPin a commit and review evidence before release.\n",
    "java/pom.xml": '''<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>example.fixture</groupId>
  <artifactId>payment-java</artifactId>
  <version>1.0.0</version>
  <dependencies>
    <dependency>
      <groupId>org.slf4j</groupId>
      <artifactId>slf4j-api</artifactId>
      <version>2.0.16</version>
    </dependency>
  </dependencies>
</project>
''',
    "java/src/main/java/example/Payment.java": '''package example;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
public class Payment {
    private static final Logger LOG = LoggerFactory.getLogger(Payment.class);
    public String release() { return "release-one"; }
}
''',
    "ts/package.json": '''{
  "name": "payment-ts",
  "version": "1.0.0",
  "scripts": {"postinstall": "touch /tmp/knowledge-git-fixture-must-not-run"},
  "dependencies": {"lodash": "4.17.21"}
}
''',
    "ts/package-lock.json": '''{
  "name": "payment-ts",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "packages": {
    "": {"name": "payment-ts", "version": "1.0.0", "dependencies": {"lodash": "4.17.21"}},
    "node_modules/lodash": {"version": "4.17.21"}
  }
}
''',
    "ts/src/payment.ts": '''import { uniq } from "lodash";
export function release(values: string[]): string[] {
  return uniq([...values, "release-one"]);
}
''',
    "go/go.mod": '''module example.test/payment-go

go 1.23

require github.com/google/uuid v1.6.0
''',
    "go/payment.go": '''package payment
import "github.com/google/uuid"
func ReleaseID() string {
    return uuid.NewString()
}
''',
    "go/legacy.go": '''package payment
func LegacyRelease() string { return "obsolete-one" }
''',
}
CHANGES = {
    "java/pom.xml": FILES["java/pom.xml"].replace("2.0.16", "2.0.17"),
    "java/src/main/java/example/Payment.java": FILES["java/src/main/java/example/Payment.java"].replace("release-one", "release-two"),
    "ts/src/payment.ts": FILES["ts/src/payment.ts"].replace("release-one", "release-two"),
    "go/payment.go": FILES["go/payment.go"].replace("uuid.NewString()", '"release-two-" + uuid.NewString()'),
}


def build_repository(root: Path):
    root.mkdir(parents=True, exist_ok=False)
    work = root / "work"
    work.mkdir()
    env = {
        "PATH": os.defpath, "LANG": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_AUTHOR_NAME": "Knowledge fixture", "GIT_AUTHOR_EMAIL": "fixture@example.test",
        "GIT_COMMITTER_NAME": "Knowledge fixture", "GIT_COMMITTER_EMAIL": "fixture@example.test",
    }

    def git(*args):
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=work, env=env, capture_output=True, check=True, timeout=15).stdout.decode().strip()

    git("init", "-q", "--initial-branch=main")
    manifest = {"kind": "fixed_three_language_git_fixture", "repository_path": "/fixture.git", "refs": {}, "commits": {}, "files": {}}
    current = dict(FILES)
    for version, date in (("v1", "2026-01-01T00:00:00+00:00"), ("v2", "2026-01-02T00:00:00+00:00")):
        if version == "v2":
            current.update(CHANGES)
            del current["go/legacy.go"]
            (work / "go/legacy.go").unlink()
        for path, content in current.items():
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode())
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
        git("add", "--all")
        git("commit", "-qm", "Fixture " + version)
        git("tag", "fixture-" + version)
        manifest["refs"][version] = "refs/tags/fixture-" + version
        manifest["commits"][version] = git("rev-parse", "HEAD")
        manifest["files"][version] = {path: {"sha256": hashlib.sha256(text.encode()).hexdigest(), "lines": len(text.splitlines())} for path, text in sorted(current.items())}
    git("clone", "--bare", "--no-local", str(work), str(root / "fixture.git"))
    git("--git-dir=" + str(root / "fixture.git"), "config", "http.receivepack", "false")
    return manifest


def make_server(root: Path, manifest: dict, host="0.0.0.0", port=8080):
    class Handler(BaseHTTPRequestHandler):
        # Git errors, request URLs and payloads do not enter fixture logs.
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.serve()

        def do_POST(self):
            self.serve()

        def serve(self):
            parsed = urlsplit(self.path)
            if self.command == "GET" and not parsed.query and parsed.path in {"/healthz", "/manifest.json"}:
                payload = manifest if parsed.path == "/manifest.json" else {"status": "ok"}
                self.reply(200, json.dumps(payload, sort_keys=True).encode(), "application/json")
                return
            if self.command == "GET":
                allowed = parsed.path == "/fixture.git/info/refs" and parsed.query == "service=git-upload-pack"
            else:
                allowed = parsed.path == "/fixture.git/git-upload-pack" and not parsed.query and self.headers.get("Content-Type") == "application/x-git-upload-pack-request"
            if not allowed:
                self.reply(404, b"Not found", "text/plain")
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                length = int(lengths[0]) if lengths else 0
                if len(lengths) > 1 or self.headers.get("Transfer-Encoding") or not 0 <= length <= 1_048_576:
                    raise ValueError
            except ValueError:
                self.reply(400, b"Invalid request", "text/plain")
                return
            self.connection.settimeout(10)
            env = {
                "PATH": os.defpath, "GIT_PROJECT_ROOT": str(root), "GIT_HTTP_EXPORT_ALL": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                "PATH_INFO": parsed.path, "QUERY_STRING": parsed.query, "REQUEST_METHOD": self.command,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""), "CONTENT_LENGTH": str(length),
                "REMOTE_ADDR": self.client_address[0],
            }
            if self.headers.get("Git-Protocol") == "version=2":
                env["GIT_PROTOCOL"] = "version=2"
            try:
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError
                result = subprocess.run(["git", "http-backend"], input=data, env=env, capture_output=True, check=True, timeout=15).stdout
                headers, body = result.split(b"\r\n\r\n", 1)
                status, content_type = 200, "application/octet-stream"
                for line in headers.decode("ascii").split("\r\n"):
                    key, value = line.split(":", 1)
                    if key.lower() == "status":
                        status = int(value.strip().split()[0])
                    elif key.lower() == "content-type":
                        content_type = value.strip()
                self.reply(status, body, content_type)
            except (OSError, ValueError, subprocess.SubprocessError):
                self.reply(503, b"Git fixture unavailable", "text/plain")

        def reply(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="knowledge-git-http-") as directory:
        root = Path(directory) / "repositories"
        manifest = build_repository(root)
        make_server(root, manifest).serve_forever()