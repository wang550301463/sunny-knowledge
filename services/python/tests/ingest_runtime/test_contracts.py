import pytest

from knowledge_platform.ingest.schemas import SourceCreate, SourceUpdate
from knowledge_platform.ingest.connectors import GitConnector, SnapshotLimits, uploaded_snapshot
from knowledge_platform.ingest.compiler import KnowledgeCompiler
from knowledge_platform.ingest.analyzers import SourceSnapshot, SourceFile, analyze_snapshot

@pytest.mark.parametrize('url', ['file:///etc/passwd','-x','https://u:p@example.com/x','https://host/x?q=1','ssh://git@host/x','https://host/x#f','ext::evil','/tmp/repo'])
def test_rejects_git_urls_that_can_override_transport_or_carry_credentials(url):
    with pytest.raises(ValueError):
        SourceCreate(name='repo',space_id='s',kind='git',config={'url':url,'ref':'HEAD'})

def test_upload_bytes_preserve_crlf_and_utf8_exactly():
    source=SourceCreate(name='guide',space_id='s',kind='markdown',config={'path':'guide.md','content':'# x\r\n原文\n'})
    result=uploaded_snapshot(source.kind,source.config,1,SnapshotLimits())
    assert result.files[0].data == b'# x\r\n\xe5\x8e\x9f\xe6\x96\x87\n'
    assert result.revision == 'version:1'

def test_binary_upload_is_rejected_without_replacement_text():
    with pytest.raises(ValueError):
        SourceCreate(name='x',space_id='s',kind='markdown',config={'path':'x.md','content':'\x00'})

def test_ticket_requires_exact_json_object():
    with pytest.raises(ValueError):
        SourceCreate(name='x',space_id='s',kind='ticket',config={'path':'x.json','content':'[]'})

def test_update_requires_base_version():
    with pytest.raises(ValueError):
        SourceUpdate(name='x',config={'path':'x.md','content':'x'})

def test_code_compiler_uses_source_namespace_and_registered_snapshot_evidence():
    snapshot=SourceSnapshot('src-a','commit', (SourceFile('main.go',b'package main\nfunc Pay() {}\n'),))
    analysis=analyze_snapshot(snapshot)
    compiler=KnowledgeCompiler()
    pages=compiler.code_pages(analysis,{'main.go':{'id':'snap-1','resource_id':'source:src-a','source_id':'src-a','source_revision':'commit','path':'main.go','kind':'code','text':'package main\nfunc Pay() {}\n'}})
    assert pages and pages[0]['content']['entity_type']=='File'
    assert all(c['kind']=='fact' for c in pages[0]['content']['claims'])
    assert pages[0]['content']['evidence'][0]['revision_id']=='snap-1'
    other=analyze_snapshot(SourceSnapshot('src-b','commit',snapshot.files))
    assert pages[0]['page_id'] != compiler.code_pages(other,{'main.go':{'id':'snap-2','resource_id':'source:src-b','source_id':'src-b','source_revision':'commit','path':'main.go','kind':'code','text':'package main\nfunc Pay() {}\n'}})[0]['page_id']
    assert 'Procedure' not in str(pages)