import hashlib
import pytest
from botocore.exceptions import ClientError
from knowledge_platform.ingest.storage import S3Store
from knowledge_platform.ingest.connectors import RawFile, RawSnapshot
from knowledge_platform.ingest.schemas import IngestError

class ObjectServer:
    def __init__(self): self.objects={}; self.puts=[]
    def put_object(self, **kw):
        self.puts.append(kw)
        assert kw['IfNoneMatch']=='*'
        if kw['Key'] in self.objects:
            raise ClientError({'Error':{'Code':'PreconditionFailed'},'ResponseMetadata':{'HTTPStatusCode':412}},'PutObject')
        self.objects[kw['Key']]=kw['Body']
    def get_object(self, **kw):
        from io import BytesIO
        return {'Body':BytesIO(self.objects[kw['Key']])}

def test_immutable_object_retry_reads_hash_and_does_not_overwrite():
    server=ObjectServer(); store=S3Store(server,'raw')
    first=store.put_bytes(b'original')
    assert store.put_bytes(b'original')==first
    assert server.objects[first]==b'original'
    server.objects[first]=b'corrupted'
    with pytest.raises(IngestError,match='checksum'):
        store.put_bytes(b'original')

def test_manifest_preserves_exact_bytes_and_revision():
    server=ObjectServer(); store=S3Store(server,'raw')
    snapshot=RawSnapshot('abc123',(RawFile('a.md',b'a\r\nb\n','markdown'),RawFile('bad.go',b'\xff','raw',('invalid_utf8',))))
    key, manifest=store.put_snapshot('source-id',1,snapshot)
    assert manifest['source_revision']=='abc123'
    assert manifest['files'][0]['sha256']==hashlib.sha256(b'a\r\nb\n').hexdigest()
    assert store.get_bytes(manifest['files'][0]['object_key'],manifest['files'][0]['sha256'])==b'a\r\nb\n'
    assert 'objects/sha256/' in key