#!/usr/bin/env python3
"""Backup/restore the immutable raw S3 store. Database consistency is a separate step.

Run in a private backup directory on an encrypted disk. No credentials are stored
in the manifest. Restore only into an empty bucket or an exact partial restore.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

KEY = re.compile(r"objects/sha256/([a-f0-9]{2})/([a-f0-9]{64})\Z")
MAX_OBJECTS = 1_000_000
MAX_OBJECT_BYTES = 1_073_741_824
MAX_MANIFEST_BYTES = 512 * 1024 * 1024
BLOCK = 1024 * 1024


class SnapshotError(Exception):
    """Safe operational code; never include provider exception bodies."""


def inventory(client, bucket):
    result, cursor, seen = {}, None, set()
    while True:
        arguments = {"Bucket": bucket, "MaxKeys": 1000}
        if cursor:
            arguments["ContinuationToken"] = cursor
        response = client.list_objects_v2(**arguments)
        for item in response.get("Contents", []):
            key, size = item.get("Key"), item.get("Size")
            if not isinstance(key, str) or key in result or type(size) is not int or not 0 <= size <= MAX_OBJECT_BYTES:
                raise SnapshotError("invalid_inventory")
            result[key] = (size, item.get("ETag"))
            if len(result) > MAX_OBJECTS:
                raise SnapshotError("inventory_limit")
        if not response.get("IsTruncated", False):
            return result
        cursor = response.get("NextContinuationToken")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise SnapshotError("invalid_inventory")
        seen.add(cursor)


def digest_for_key(key):
    match = KEY.fullmatch(key)
    if not match or match[1] != match[2][:2]:
        raise SnapshotError("unsupported_object_key")
    return match[2]


def private_open(path, *, write=False):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL if write else os.O_RDONLY
    fd = os.open(path, flags | os.O_NOFOLLOW)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise SnapshotError("backup_integrity")
    if write:
        os.fchmod(fd, 0o600)
    return os.fdopen(fd, "wb" if write else "rb")


def copy_checked(stream, digest, size, destination=None, *, code="backup_integrity"):
    checksum, received = hashlib.sha256(), 0
    while True:
        chunk = stream.read(min(BLOCK, size - received + 1))
        if not chunk:
            break
        received += len(chunk)
        if received > size:
            raise SnapshotError(code)
        checksum.update(chunk)
        if destination:
            destination.write(chunk)
    if received != size or checksum.hexdigest() != digest:
        raise SnapshotError(code)


def remote_checked(client, bucket, item, destination=None, *, code="backup_integrity"):
    response = client.get_object(Bucket=bucket, Key=item["key"])
    stream = response["Body"]
    try:
        if response.get("ContentLength") != item["size"]:
            raise SnapshotError(code)
        copy_checked(stream, item["sha256"], item["size"], destination, code=code)
    finally:
        stream.close()


def backup(client, bucket, directory):
    directory = Path(directory)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        raise SnapshotError("backup_exists") from None
    try:
        (directory / "objects").mkdir(mode=0o700)
        before = inventory(client, bucket)
        objects = []
        for key, (size, _) in sorted(before.items()):
            digest = digest_for_key(key)
            item = {"key": key, "sha256": digest, "size": size, "file": "objects/" + digest}
            with private_open(directory / item["file"], write=True) as output:
                remote_checked(client, bucket, item, output)
                output.flush()
                os.fsync(output.fileno())
            objects.append(item)
        if inventory(client, bucket) != before:
            raise SnapshotError("source_changed")
        manifest = {
            "version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "object_count": len(objects),
            "total_bytes": sum(item["size"] for item in objects),
            "objects": objects,
        }
        with private_open(directory / "manifest.pending", write=True) as output:
            output.write(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())
            output.flush()
            os.fsync(output.fileno())
        os.replace(directory / "manifest.pending", directory / "manifest.json")
        return {"object_count": manifest["object_count"], "total_bytes": manifest["total_bytes"]}
    except (BotoCoreError, ClientError):
        raise SnapshotError("storage_unavailable") from None
    except OSError:
        raise SnapshotError("backup_filesystem") from None


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SnapshotError("backup_integrity")
        value[key] = item
    return value


def verify(directory):
    directory = Path(directory)
    try:
        if directory.is_symlink() or (directory / "objects").is_symlink():
            raise SnapshotError("backup_integrity")
        with private_open(directory / "manifest.json") as source:
            if os.fstat(source.fileno()).st_size > MAX_MANIFEST_BYTES:
                raise SnapshotError("backup_integrity")
            manifest = json.loads(source.read(), object_pairs_hook=unique_object)
        if not isinstance(manifest, dict) or set(manifest) != {"version", "created_at", "objects", "object_count", "total_bytes"}:
            raise SnapshotError("backup_integrity")
        objects = manifest["objects"]
        if type(manifest["version"]) is not int or manifest["version"] != 1 or not isinstance(objects, list) or len(objects) > MAX_OBJECTS:
            raise SnapshotError("backup_integrity")
        created = datetime.fromisoformat(manifest["created_at"])
        if created.utcoffset() is None:
            raise SnapshotError("backup_integrity")
        seen, total = set(), 0
        for item in objects:
            if not isinstance(item, dict) or set(item) != {"key", "sha256", "size", "file"}:
                raise SnapshotError("backup_integrity")
            digest = digest_for_key(item["key"])
            if digest in seen or item["sha256"] != digest or item["file"] != "objects/" + digest or type(item["size"]) is not int or not 0 <= item["size"] <= MAX_OBJECT_BYTES:
                raise SnapshotError("backup_integrity")
            seen.add(digest)
            total += item["size"]
            with private_open(directory / item["file"]) as source:
                copy_checked(source, digest, item["size"])
        if (
            type(manifest["object_count"]) is not int
            or manifest["object_count"] != len(objects)
            or type(manifest["total_bytes"]) is not int
            or manifest["total_bytes"] != total
            or {entry.name for entry in (directory / "objects").iterdir()} != seen
        ):
            raise SnapshotError("backup_integrity")
        return manifest
    except (OSError, ValueError, TypeError, KeyError, SnapshotError):
        raise SnapshotError("backup_integrity") from None


def restore(client, bucket, directory):
    # Complete local validation and all existing-object checks precede the first write.
    manifest = verify(directory)
    expected = {item["key"]: item for item in manifest["objects"]}
    try:
        existing = inventory(client, bucket)
        if not existing.keys() <= expected.keys():
            raise SnapshotError("target_conflict")
        for key, (size, _) in existing.items():
            if size != expected[key]["size"]:
                raise SnapshotError("target_conflict")
            remote_checked(client, bucket, expected[key], code="target_conflict")
        restored = 0
        for key, item in expected.items():
            if key in existing:
                continue
            # The backup directory must remain private and unmodified during restore.
            with private_open(Path(directory) / item["file"]) as source:
                copy_checked(source, item["sha256"], item["size"])
                source.seek(0)
                try:
                    client.put_object(Bucket=bucket, Key=key, Body=source, IfNoneMatch="*", Metadata={"sha256": item["sha256"]})
                except (BotoCoreError, ClientError):
                    # A lost response is not authorization to overwrite/retry a PUT.
                    # Only exact bytes already persisted can resolve uncertainty.
                    remote_checked(client, bucket, item, code="target_conflict")
                else:
                    remote_checked(client, bucket, item, code="target_conflict")
                restored += 1
        final = inventory(client, bucket)
        if final.keys() != expected.keys() or any(final[key][0] != item["size"] for key, item in expected.items()):
            raise SnapshotError("target_conflict")
        return {"restored": restored, "object_count": len(expected), "total_bytes": manifest["total_bytes"]}
    except (BotoCoreError, ClientError):
        raise SnapshotError("storage_unavailable") from None
    except OSError:
        raise SnapshotError("backup_filesystem") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "verify", "restore"])
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--bucket")
    args = parser.parse_args()
    try:
        if args.action == "verify":
            manifest = verify(args.directory)
            result = {"object_count": manifest["object_count"], "total_bytes": manifest["total_bytes"]}
        else:
            if not args.bucket:
                parser.error("--bucket is required for S3 operations")
            client = boto3.client(
                "s3", endpoint_url=os.environ["SNAPSHOT_S3_ENDPOINT_URL"],
                aws_access_key_id=os.environ["SNAPSHOT_S3_ACCESS_KEY_ID"],
                aws_secret_access_key=os.environ["SNAPSHOT_S3_SECRET_ACCESS_KEY"],
                region_name=os.environ.get("SNAPSHOT_S3_REGION", "us-east-1"),
                config=Config(connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 1}, s3={"addressing_style": "path"}),
            )
            result = (backup if args.action == "backup" else restore)(client, args.bucket, args.directory)
        print(json.dumps({"status": "verified", **result}, sort_keys=True))
    except SnapshotError as error:
        parser.exit(1, str(error) + "\n")
    except (KeyError, ValueError, BotoCoreError, ClientError, OSError):
        parser.exit(1, "snapshot_configuration_or_transport_error\n")


if __name__ == "__main__":
    main()
