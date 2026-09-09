"""Bounded connectors. Git reads a fresh bare object database and never checks out code."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from .schemas import IngestError, MAX_FILE_BYTES, validate_config, validate_path


@dataclass(frozen=True)
class SnapshotLimits:
    max_bytes: int = 64_000_000
    max_files: int = 10_000
    max_file_bytes: int = MAX_FILE_BYTES
    max_git_disk_bytes: int = 256_000_000
    timeout_seconds: int = 120


@dataclass(frozen=True)
class RawFile:
    path: str
    data: bytes = field(repr=False)
    kind: str
    diagnostics: tuple[str, ...] = ()

    @property
    def digest(self):
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class RawSnapshot:
    revision: str
    files: tuple[RawFile, ...]
    diagnostics: tuple[str, ...] = ()


class Connector(Protocol):
    def capture(self, config: dict, credential: dict | None, version: int) -> RawSnapshot: ...


def classify(path: str, data: bytes, default='code') -> tuple[str, tuple[str, ...]]:
    try:
        data.decode('utf-8', errors='strict')
    except UnicodeError:
        return 'raw', ('invalid_utf8',)
    if any(c < 32 and c not in (9, 10, 12, 13) for c in data):
        return 'raw', ('binary_file',)
    if default == 'code' and path.lower().endswith(('.md', '.markdown')):
        return 'markdown', ()
    return default, ()


def uploaded_snapshot(kind: str, config: dict, version: int, limits: SnapshotLimits):
    validate_config(kind, config)
    data = config['content'].encode('utf-8')
    if len(data) > min(limits.max_file_bytes, limits.max_bytes):
        raise IngestError(422, 'source_limit_exceeded', 'Source exceeds configured byte budget')
    return RawSnapshot(f'version:{version}', (RawFile(config['path'], data, kind),))


class UploadConnector:
    def __init__(self, kind: str, limits: SnapshotLimits):
        self.kind, self.limits = kind, limits

    def capture(self, config, credential, version):
        return uploaded_snapshot(self.kind, config, version, self.limits)


class GitConnector:
    def __init__(self, limits: SnapshotLimits | None = None):
        self.limits = limits or SnapshotLimits()
        self.git = shutil.which('git')
        if self.git is None:
            raise IngestError(503, 'dependency_unavailable', 'Git executable unavailable')

    def _run(self, args, root: Path, env: dict, max_output=1_000_000):
        # stdout/stderr are never logged; bounded temp outputs prevent subprocess buffering OOM.
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            proc = subprocess.Popen([self.git, '-c', 'core.hooksPath=/dev/null',
                '-c', 'protocol.allow=never', '-c', 'protocol.http.allow=always',
                '-c', 'protocol.https.allow=always', '-c', 'protocol.ssh.allow=always',
                '-c', 'credential.helper=', '-c', 'http.followRedirects=false',
                *args], cwd=root, env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=err, start_new_session=True)
            start = time.monotonic()
            try:
                while proc.poll() is None:
                    disk = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
                    if (time.monotonic() - start > self.limits.timeout_seconds
                            or disk > self.limits.max_git_disk_bytes
                            or os.fstat(out.fileno()).st_size > max_output
                            or os.fstat(err.fileno()).st_size > 1_000_000):
                        raise IngestError(422, 'source_limit_exceeded', 'Git transfer exceeds budget')
                    time.sleep(.03)
                if proc.returncode != 0:
                    raise IngestError(422, 'git_fetch_failed', 'Git operation failed; check remote, ref and credentials')
                if os.fstat(out.fileno()).st_size > max_output:
                    raise IngestError(422, 'source_limit_exceeded', 'Git output exceeds budget')
                out.seek(0)
                return out.read(max_output + 1)
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()

    @staticmethod
    def _env(root: Path):
        return {'PATH': os.defpath, 'HOME': str(root), 'LANG': 'C.UTF-8',
                'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                'GIT_CONFIG_SYSTEM': '/dev/null', 'GIT_TERMINAL_PROMPT': '0',
                'GIT_ATTR_NOSYSTEM': '1', 'GIT_LFS_SKIP_SMUDGE': '1'}

    def capture(self, config, credential, version):
        validate_config('git', config)
        credential = credential or {}
        with tempfile.TemporaryDirectory(prefix='knowledge-ingest-') as directory:
            root = Path(directory)
            env = self._env(root)
            remote = urlsplit(config['url'])
            if remote.scheme in {'http', 'https'} and credential.get('password') is not None:
                auth = base64.b64encode((credential.get('username', 'oauth2') + ':' +
                                         credential['password']).encode()).decode()
                env.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='http.extraHeader',
                           GIT_CONFIG_VALUE_0='Authorization: Basic ' + auth)
            if remote.scheme == 'ssh':
                if (not all(credential.get(k) for k in ('username', 'ssh_private_key', 'ssh_known_hosts'))
                        or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', credential['username'])):
                    raise IngestError(422, 'invalid_source', 'SSH requires username, private key and pinned known hosts')
                for name, value in [('identity', credential['ssh_private_key']),
                                    ('known_hosts', credential['ssh_known_hosts'])]:
                    path = root / name
                    path.write_text(value)
                    path.chmod(0o600)
                # All command fragments are constants or generated safe tempfile paths/usernames.
                # Git invokes this trusted transport, never a command supplied by the repository.
                env['GIT_SSH_COMMAND'] = (f'ssh -F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes '
                    f'-o StrictHostKeyChecking=yes -o UserKnownHostsFile={root}/known_hosts '
                    f'-o GlobalKnownHostsFile=/dev/null -i {root}/identity -l {credential["username"]}')
            self._run(['init', '--bare', 'objects.git'], root, env)
            self._run(['--git-dir=objects.git', 'fetch', '--no-tags', '--no-recurse-submodules',
                       '--depth=1', '--', config['url'], config['ref']], root, env)
            commit = self._run(['--git-dir=objects.git', 'rev-parse', '--verify',
                                'FETCH_HEAD^{commit}'], root, env).decode().strip()
            if not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}', commit):
                raise IngestError(422, 'invalid_source', 'Git did not resolve an immutable commit')
            return self._read_tree(root, env, 'objects.git', commit)

    def _read_tree(self, root, env, git_dir, commit):
        listing = self._run([f'--git-dir={git_dir}', 'ls-tree', '-r', '-z', '-l', commit],
                            root, env, (self.limits.max_files + 1) * 2300)
        entries = [row for row in listing.split(b'\0') if row]
        if len(entries) > self.limits.max_files:
            raise IngestError(422, 'source_limit_exceeded', 'Repository exceeds file budget')
        files, diagnostics, total = [], [], 0
        for row in entries:
            metadata, raw_path = row.split(b'\t', 1)
            mode, kind, oid, size = metadata.split()
            try:
                path = raw_path.decode('utf-8', errors='strict')
                validate_path(path)
            except (ValueError, UnicodeError):
                diagnostics.append('invalid_path_skipped')
                continue
            if mode == b'120000' or kind == b'commit':
                diagnostics.append(('symlink_skipped:' if mode == b'120000' else 'submodule_skipped:') + path)
                continue
            if kind != b'blob' or mode not in (b'100644', b'100755'):
                diagnostics.append('unsupported_file_kind:' + path)
                continue
            length = int(size)
            if length > self.limits.max_file_bytes or total + length > self.limits.max_bytes:
                raise IngestError(422, 'source_limit_exceeded', 'Repository exceeds content byte budget')
            data = self._run([f'--git-dir={git_dir}', 'cat-file', 'blob', oid.decode()],
                             root, env, self.limits.max_file_bytes)
            if len(data) != length:
                raise IngestError(422, 'invalid_source', 'Git object length mismatch')
            total += length
            file_kind, issues = classify(path, data)
            files.append(RawFile(path, data, file_kind, issues))
        return RawSnapshot(commit, tuple(files), tuple(diagnostics))