"""Content-addressed S3 with conditional writes and verified retries."""
import hashlib
import json

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .schemas import IngestError


class S3Store:
    def __init__(self, client, bucket):
        self.client, self.bucket = client, bucket

    @classmethod
    def from_settings(cls, settings):
        client = boto3.client('s3', endpoint_url=settings.ingest_s3_endpoint_url,
            aws_access_key_id=settings.ingest_s3_access_key_id,
            aws_secret_access_key=settings.ingest_s3_secret_access_key,
            region_name=settings.ingest_s3_region,
            config=Config(connect_timeout=5, read_timeout=30,
                          retries={'max_attempts': 3, 'mode': 'standard'},
                          s3={'addressing_style': 'path'}))
        return cls(client, settings.ingest_s3_bucket)

    def put_bytes(self, data: bytes):
        digest = hashlib.sha256(data).hexdigest()
        key = 'objects/sha256/' + digest[:2] + '/' + digest
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                                   IfNoneMatch='*', Metadata={'sha256': digest})
        except ClientError as error:
            status = error.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
            code = error.response.get('Error', {}).get('Code')
            if status not in {409, 412} and code not in {'PreconditionFailed', 'ConditionalRequestConflict'}:
                raise IngestError(503, 'storage_unavailable', 'Immutable raw storage unavailable') from None
            # Never trust metadata or ETag alone; verify actual stored object bytes on races/retry.
            self.get_bytes(key, digest, len(data))
        except BotoCoreError:
            raise IngestError(503, 'storage_unavailable', 'Immutable raw storage unavailable') from None
        return key

    def get_bytes(self, key, digest, max_bytes=4_000_000):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            stream = response['Body']
            try:
                data = stream.read(max_bytes + 1)
            finally:
                stream.close()
        except (ClientError, BotoCoreError):
            raise IngestError(503, 'storage_unavailable', 'Immutable raw storage unavailable') from None
        if len(data) > max_bytes or hashlib.sha256(data).hexdigest() != digest:
            raise IngestError(409, 'snapshot_conflict', 'Immutable raw object checksum mismatch')
        return data

    def put_snapshot(self, source_id, version, snapshot):
        files = []
        for file in snapshot.files:
            key = self.put_bytes(file.data)
            files.append({'path': file.path, 'size': len(file.data), 'sha256': file.digest,
                          'kind': file.kind, 'diagnostics': list(file.diagnostics), 'object_key': key})
        manifest = {'source_id': source_id, 'source_version': version,
                    'source_revision': snapshot.revision, 'files': files,
                    'file_count': len(files), 'total_bytes': sum(f['size'] for f in files),
                    'diagnostics': list(snapshot.diagnostics)}
        data = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        return self.put_bytes(data), manifest