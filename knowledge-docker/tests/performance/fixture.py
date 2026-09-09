"""Public deterministic source corpus, served only by read-only smart Git HTTP."""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def files(number, symbols):
    prefix = f"PerfRepo{number:03d}"
    go = "package performance\n" + "\n".join(
        f'func {prefix}GoOperation{i:03d}() string {{ return "go-{number}-{i}" }}'
        for i in range(symbols)
    ) + "\n"
    ts = "\n".join(
        f'export function {prefix}TsOperation{i:03d}(): string {{ return "ts-{number}-{i}"; }}'
        for i in range(symbols)
    ) + "\n"
    java = "package performance;\npublic class Operations {\n" + "\n".join(
        f'  public String {prefix}JavaOperation{i:03d}() {{ return "java-{number}-{i}"; }}'
        for i in range(symbols)
    ) + "\n}\n"
    package = {"name": f"performance-{number}", "version": "1.0.0",
               "dependencies": {"@performance/ledger": "1.2.3"}}
    return {
        "go/main.go": go,
        "go/go.mod": f"module example.invalid/performance/repo{number}\n\ngo 1.23\n\nrequire example.invalid/performance/ledger v1.2.3\n",
        "ts/main.ts": ts,
        "ts/package.json": json.dumps(package, sort_keys=True) + "\n",
        "ts/package-lock.json": json.dumps({"name": package["name"], "lockfileVersion": 3,
            "packages": {"": package, "node_modules/@performance/ledger": {"version": "1.2.3"}}}, sort_keys=True) + "\n",
        "java/src/main/java/performance/Operations.java": java,
        "java/pom.xml": f"<project><modelVersion>4.0.0</modelVersion><groupId>performance</groupId><artifactId>repo{number}</artifactId><version>1.0.0</version><dependencies><dependency><groupId>performance</groupId><artifactId>ledger</artifactId><version>1.2.3</version></dependency></dependencies></project>\n",
    }


def build(root, *, repositories=100, symbols=340):
    if type(repositories) is not int or not 1 <= repositories <= 100 or type(symbols) is not int or not 1 <= symbols <= 800:
        raise ValueError("Corpus bounds exceeded")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    env = {"PATH": os.defpath, "LANG": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_AUTHOR_NAME": "Public performance fixture", "GIT_COMMITTER_NAME": "Public performance fixture",
           "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
           "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00"}
    manifest = {"kind": "public_performance_three_language_v1", "symbols_per_language": symbols, "repositories": []}
    for number in range(repositories):
        work = root / f"work-{number:03d}"
        work.mkdir()
        def git(*args):
            return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=work,
                                  env=env, capture_output=True, check=True, timeout=30).stdout.decode().strip()
        git("init", "-q", "--initial-branch=main")
        corpus = files(number, symbols)
        for name, text in corpus.items():
            destination = work / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(text.encode())
        git("add", "--all")
        git("commit", "-qm", f"Public performance repository {number:03d}")
        commit = git("rev-parse", "HEAD")
        git("tag", "performance-v1")
        name = f"repo-{number:03d}"
        bare = root / (name + ".git")
        git("clone", "--bare", "--no-local", str(work), str(bare))
        git("--git-dir=" + str(bare), "config", "http.receivepack", "false")
        manifest["repositories"].append({"name": name, "path": "/" + name + ".git", "ref": "refs/tags/performance-v1",
            "commit": commit, "files": {p: hashlib.sha256(v.encode()).hexdigest() for p, v in sorted(corpus.items())},
            "queries": [{"query": f"PerfRepo{number:03d}{language}Operation000", "path": path} for language, path in
                (("Go", "go/main.go"), ("Ts", "ts/main.ts"), ("Java", "java/src/main/java/performance/Operations.java"))]})
    manifest["digest"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return manifest


def server(root, manifest, host="0.0.0.0", port=8080):
    allowed = {repo["path"] for repo in manifest["repositories"]}
    permits = threading.BoundedSemaphore(4)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.respond()
        def do_POST(self):
            self.respond()
        def reply(self, status, payload, media="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def respond(self):
            parsed = urlsplit(self.path)
            if self.headers.get("Authorization") or self.headers.get("Transfer-Encoding"):
                self.reply(400, b"{}")
                return
            if self.command == "GET" and not parsed.query and parsed.path in {"/healthz", "/manifest.json"}:
                self.reply(200, json.dumps(manifest if parsed.path == "/manifest.json" else {"kind": manifest["kind"], "status": "ok"}).encode())
                return
            valid = any((self.command == "GET" and parsed.path == repo + "/info/refs" and parsed.query == "service=git-upload-pack") or
                        (self.command == "POST" and parsed.path == repo + "/git-upload-pack" and not parsed.query and
                         self.headers.get("Content-Type") == "application/x-git-upload-pack-request") for repo in allowed)
            if not valid:
                self.reply(404, b"{}")
                return
            if not permits.acquire(blocking=False):
                self.reply(503, b"{}")
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                size = int(lengths[0]) if lengths else 0
                if len(lengths) > 1 or not 0 <= size <= 1_048_576:
                    raise ValueError
                self.connection.settimeout(10)
                data = self.rfile.read(size)
                if len(data) != size:
                    raise ValueError
                env = {"PATH": os.defpath, "GIT_PROJECT_ROOT": str(root), "GIT_HTTP_EXPORT_ALL": "1",
                       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                       "PATH_INFO": parsed.path, "QUERY_STRING": parsed.query, "REQUEST_METHOD": self.command,
                       "CONTENT_TYPE": self.headers.get("Content-Type", ""), "CONTENT_LENGTH": str(size),
                       "REMOTE_ADDR": self.client_address[0]}
                if self.headers.get("Git-Protocol") == "version=2": env["GIT_PROTOCOL"] = "version=2"
                output = subprocess.run(["git", "http-backend"], input=data, env=env, capture_output=True, check=True, timeout=30).stdout
                header, body = output.split(b"\r\n\r\n", 1)
                status, media = 200, "application/octet-stream"
                for line in header.decode("ascii").split("\r\n"):
                    key, value = line.split(":", 1)
                    if key.lower() == "content-type": media = value.strip()
                    if key.lower() == "status": status = int(value.strip().split()[0])
                self.reply(status, body, media)
            except (ValueError, OSError, subprocess.SubprocessError):
                self.reply(503, b"{}")
            finally:
                permits.release()
    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repositories", type=int, default=100)
    parser.add_argument("--symbols", type=int, default=340)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="public-performance-") as directory:
        root = Path(directory) / "repositories"
        manifest = build(root, repositories=args.repositories, symbols=args.symbols)
        server(root, manifest).serve_forever()
