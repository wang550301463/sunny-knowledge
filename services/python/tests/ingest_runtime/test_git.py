"""Actual Git smart-HTTP fixture; repository programs are deliberately hostile and never run."""
import os
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from knowledge_platform.ingest.connectors import GitConnector, SnapshotLimits
from knowledge_platform.ingest.schemas import IngestError, SourceCreate


def git(root, *args):
    env={**os.environ,'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1',
         'GIT_AUTHOR_NAME':'Fixture','GIT_AUTHOR_EMAIL':'fixture@example.test',
         'GIT_COMMITTER_NAME':'Fixture','GIT_COMMITTER_EMAIL':'fixture@example.test'}
    return subprocess.run(['git',*args],cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout

@pytest.fixture
def repository(tmp_path):
    root=tmp_path/'server';root.mkdir()
    work=tmp_path/'work';work.mkdir()
    git(work,'init','-q')
    (work/'main.go').write_bytes(b'package main\r\nfunc Pay() {}\n')
    (work/'invalid.go').write_bytes(b'\xff\x00')
    (work/'link.go').symlink_to('/etc/passwd')
    (work/'package.json').write_text('{"scripts":{"postinstall":"touch SHOULD_NOT_RUN"}}')
    (work/'.gitattributes').write_text('*.go filter=evil\n')
    git(work,'add','.')
    git(work,'commit','-qm','fixture')
    commit=git(work,'rev-parse','HEAD').decode().strip()
    git(root,'clone','--bare',str(work),'repo.git')
    return root,commit

@contextmanager
def smart_http(root):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self): self.serve()
        def do_POST(self): self.serve()
        def serve(self):
            parsed=urlsplit(self.path)
            length=int(self.headers.get('Content-Length','0'))
            env={'PATH':os.defpath,'GIT_PROJECT_ROOT':str(root),'GIT_HTTP_EXPORT_ALL':'1',
                 'PATH_INFO':parsed.path,'QUERY_STRING':parsed.query,'REQUEST_METHOD':self.command,
                 'CONTENT_TYPE':self.headers.get('Content-Type',''),'CONTENT_LENGTH':str(length),
                 'REMOTE_ADDR':'127.0.0.1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'}
            result=subprocess.run(['git','http-backend'],env=env,input=self.rfile.read(length),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout
            headers,body=result.split(b'\r\n\r\n',1)
            self.send_response(200)
            for line in headers.split(b'\r\n'):
                k,v=line.decode().split(':',1)
                if k.lower()!='status':self.send_header(k,v.strip())
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}/repo.git'
    finally:server.shutdown();server.server_close();thread.join()


def test_real_git_fetch_exact_commit_without_checkout_filters_hooks_or_scripts(repository,tmp_path,monkeypatch):
    root,commit=repository
    marker=tmp_path/'EXECUTED'
    config=tmp_path/'hostile-global'
    config.write_text(f'[filter "evil"]\n smudge = touch {marker}\n required = true\n[core]\n hooksPath = {tmp_path}/hooks\n')
    hooks=tmp_path/'hooks';hooks.mkdir()
    hook=hooks/'reference-transaction';hook.write_text(f'#!/bin/sh\ntouch {marker}\n');hook.chmod(0o755)
    monkeypatch.setenv('GIT_CONFIG_GLOBAL',str(config))
    monkeypatch.setenv('GIT_CONFIG_COUNT','1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0','core.hooksPath')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0',str(hooks))
    with smart_http(root) as url:
        snapshot=GitConnector().capture({'url':url,'ref':'HEAD'},None,1)
    assert snapshot.revision==commit
    files={f.path:f for f in snapshot.files}
    assert files['main.go'].data==b'package main\r\nfunc Pay() {}\n'
    assert files['invalid.go'].kind=='raw'
    assert 'invalid_utf8' in files['invalid.go'].diagnostics
    assert 'link.go' not in files
    assert 'symlink_skipped:link.go' in snapshot.diagnostics
    assert not marker.exists()
    assert not any(root.rglob('SHOULD_NOT_RUN'))

@pytest.mark.parametrize('limits',[SnapshotLimits(max_files=1),SnapshotLimits(max_file_bytes=4),SnapshotLimits(max_bytes=8)])
def test_real_git_rejects_incomplete_snapshots_at_configured_budgets(repository,limits):
    root,_=repository
    with smart_http(root) as url,pytest.raises(IngestError) as exc:
        GitConnector(limits).capture({'url':url,'ref':'HEAD'},None,1)
    assert exc.value.code=='source_limit_exceeded'

def test_reserved_manifest_prefix_cannot_be_uploaded():
    with pytest.raises(ValueError):
        SourceCreate(name='x',space_id='s',kind='markdown',config={'path':'.__knowledge__/manifest.json','content':'forged'})