"""Auto-sync and OSS connector unit tests (no network: boto3 is stubbed)."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from knowledge_platform.ingest.auto_sync import _connector_for
from knowledge_platform.ingest.connectors import OSSConnector, SnapshotLimits
from knowledge_platform.ingest.schemas import IngestError, validate_config


class TestOSSConfig:
    def test_valid_config(self):
        config = {'endpoint': 'https://oss-cn-hangzhou.aliyuncs.com', 'bucket': 'knowledge-raw', 'prefix': 'docs/'}
        assert validate_config('oss', config) is config

    def test_rejects_credentials_in_url(self):
        with pytest.raises(ValueError):
            validate_config('oss', {'endpoint': 'https://ak:sk@oss.example.com', 'bucket': 'bucket-1', 'prefix': ''})

    def test_rejects_bad_bucket(self):
        with pytest.raises(ValueError):
            validate_config('oss', {'endpoint': 'https://oss.example.com', 'bucket': 'Bad_Bucket!', 'prefix': ''})

    def test_rejects_absolute_prefix(self):
        with pytest.raises(ValueError):
            validate_config('oss', {'endpoint': 'https://oss.example.com', 'bucket': 'bucket-1', 'prefix': '/etc'})

    def test_requires_exact_keys(self):
        with pytest.raises(ValueError):
            validate_config('oss', {'endpoint': 'https://oss.example.com', 'bucket': 'bucket-1'})


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, limit=-1):
        return self._data[:limit] if limit > 0 else self._data

    def close(self):
        pass


class _FakeClient:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects
        self.calls = []

    def list_objects_v2(self, **kwargs):
        self.calls.append(('list', kwargs['Prefix']))
        keys = sorted(k for k in self.objects if k.startswith(kwargs['Prefix']))
        page = keys[:kwargs['MaxKeys']]
        return {'Contents': [{'Key': k, 'ETag': f'"etag-{k}"', 'Size': len(self.objects[k])} for k in page],
                'IsTruncated': False}

    def get_object(self, **kwargs):
        self.calls.append(('get', kwargs['Key']))
        return {'Body': _FakeBody(self.objects[kwargs['Key']])}


class TestOSSConnector:
    def _capture(self, objects, prefix='docs/'):
        connector = OSSConnector(SnapshotLimits())
        client = _FakeClient(objects)
        connector._client = lambda config, credential: client
        snapshot = connector.capture(
            {'endpoint': 'https://oss.example.com', 'bucket': 'bucket-1', 'prefix': prefix},
            {'username': 'ak', 'password': 'sk'}, 1)
        return snapshot, client

    def test_capture_files_and_revision(self):
        snapshot, _ = self._capture({'docs/a.md': b'# hello', 'docs/b.txt': 'text'.encode()})
        assert [f.path for f in snapshot.files] == ['docs/a.md', 'docs/b.txt']
        assert snapshot.files[0].kind == 'markdown'
        assert snapshot.revision.startswith('oss:')

    def test_revision_deterministic_and_change_sensitive(self):
        first, _ = self._capture({'docs/a.md': b'one'})
        same, _ = self._capture({'docs/a.md': b'one'})
        changed, _ = self._capture({'docs/a.md': b'two'})
        assert first.revision == same.revision
        assert first.revision != changed.revision

    def test_invalid_paths_skipped_with_diagnostics(self):
        snapshot, _ = self._capture({'docs/ok.md': b'x', 'docs/../evil': b'y'})
        assert [f.path for f in snapshot.files] == ['docs/ok.md']
        assert any('invalid_path_skipped' in d for d in snapshot.diagnostics)

    def test_connector_factory(self):
        assert isinstance(_connector_for('git', SnapshotLimits()).__class__.__name__, str)
        assert _connector_for('oss', SnapshotLimits()).__class__ is OSSConnector
        with pytest.raises(IngestError):
            _connector_for('markdown', SnapshotLimits())


class TestSourceSchemas:
    def test_create_allows_oss_with_credentials_and_auto_sync(self):
        from knowledge_platform.ingest.schemas import SourceCreate
        body = SourceCreate(name='oss docs', space_id='space', kind='oss',
                            config={'endpoint': 'https://oss.example.com', 'bucket': 'bucket-1', 'prefix': ''},
                            credential={'username': 'ak', 'password': 'sk'},
                            auto_sync=True, auto_sync_interval_seconds=600)
        assert body.auto_sync is True

    def test_create_rejects_auto_sync_for_uploads(self):
        from knowledge_platform.ingest.schemas import SourceCreate
        with pytest.raises(ValueError):
            SourceCreate(name='m', space_id='s', kind='markdown',
                         config={'path': 'a.md', 'content': '# x'}, auto_sync=True)

    def test_interval_bounds(self):
        from knowledge_platform.ingest.schemas import SourceCreate
        with pytest.raises(Exception):
            SourceCreate(name='m', space_id='s', kind='oss',
                         config={'endpoint': 'https://o.e.com', 'bucket': 'bb', 'prefix': ''},
                         auto_sync_interval_seconds=10)
