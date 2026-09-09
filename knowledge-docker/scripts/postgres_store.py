#!/usr/bin/env python3
"""Private, per-database PG17 archives; restore exclusively into new databases.

Only trusted archives/manifests are supported: PostgreSQL archives contain executable
SQL. Checksums detect corruption, not a malicious party replacing both files and manifest.
Credentials remain in the subprocess environment; provider stderr is never returned.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from uuid import uuid4


class PostgresStoreError(RuntimeError):
    pass


IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z", re.ASCII)
DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
MAX_DATABASES = 64
MAX_MANIFEST = 1024 * 1024
MAX_TOC = 64 * 1024 * 1024


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise PostgresStoreError("invalid_identifier")
    return value


def quote(value):
    return '"' + identifier(value) + '"'


def records(values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_DATABASES:
        raise PostgresStoreError("invalid_database_mapping")
    services, databases = set(), set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {"service", "database", "owner", "connect_roles"}:
            raise PostgresStoreError("invalid_database_mapping")
        for key in ("service", "database", "owner"):
            identifier(value[key])
        roles = value["connect_roles"]
        if not isinstance(roles, list) or not 1 <= len(roles) <= MAX_DATABASES:
            raise PostgresStoreError("invalid_database_mapping")
        for role in roles:
            identifier(role)
        if len(set(roles)) != len(roles) or value["owner"] not in roles:
            raise PostgresStoreError("invalid_database_mapping")
        if value["service"] in services or value["database"] in databases:
            raise PostgresStoreError("duplicate_database_mapping")
        services.add(value["service"])
        databases.add(value["database"])
    return values


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PostgresStoreError("duplicate_json_key")
        result[key] = value
    return result


def private_open(path, mode="rb"):
    flags = os.O_RDONLY if mode == "rb" else os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077 or meta.st_uid != os.getuid():
            os.close(fd)
            raise PostgresStoreError("private_file_required")
        return os.fdopen(fd, mode)
    except OSError:
        raise PostgresStoreError("private_file_unavailable") from None


def private_json(path):
    with private_open(path) as source:
        data = source.read(MAX_MANIFEST + 1)
    if len(data) > MAX_MANIFEST:
        raise PostgresStoreError("json_size_limit")
    try:
        return json.loads(data, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError):
        raise PostgresStoreError("invalid_json") from None


def digest_file(file):
    file.seek(0)
    digest, size = hashlib.sha256(), 0
    while block := file.read(1024 * 1024):
        digest.update(block)
        size += len(block)
    file.seek(0)
    return digest.hexdigest(), size


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_json(path, value):
    path = Path(path)
    temporary = path.parent / (".pending-" + uuid4().hex)
    try:
        with private_open(temporary, "wb") as output:
            output.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n")
            output.flush()
            os.fsync(output.fileno())
        # Link is atomic and refuses to overwrite an existing manifest/receipt.
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        sync_directory(path.parent)
    except OSError:
        raise PostgresStoreError("publication_failed") from None
    finally:
        temporary.unlink(missing_ok=True)


class PostgresTools:
    """Local PG17.6 binaries or official binaries inside one explicit Docker container."""

    def __init__(self, connection, docker_container=None, timeout=3600):
        keys = {"host", "port", "user", "password", "sslmode", "maintenance_database"}
        if not isinstance(connection, dict) or set(connection) != keys:
            raise PostgresStoreError("invalid_connection")
        for key in ("host", "password"):
            if not isinstance(connection[key], str) or not connection[key] or "\x00" in connection[key]:
                raise PostgresStoreError("invalid_connection")
        if not isinstance(connection["port"], int) or isinstance(connection["port"], bool) or not 1 <= connection["port"] <= 65535:
            raise PostgresStoreError("invalid_connection")
        identifier(connection["user"])
        identifier(connection["maintenance_database"])
        if connection["sslmode"] not in {"disable", "require", "verify-ca", "verify-full"}:
            raise PostgresStoreError("invalid_connection")
        if docker_container is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", docker_container):
            raise PostgresStoreError("invalid_tool_container")
        if not isinstance(timeout, int) or not 1 <= timeout <= 86400:
            raise PostgresStoreError("invalid_timeout")
        self.connection = dict(connection)
        self.container = docker_container
        self.timeout = timeout
        self._validated = False

    def run(self, program, args=(), database=None, stdin=None, stdout=None):
        if program not in {"pg_dump", "pg_restore", "psql"}:
            raise PostgresStoreError("unsupported_tool")
        database = identifier(database or self.connection["maintenance_database"])
        env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
        variables = {
            "PGHOST": self.connection["host"], "PGPORT": str(self.connection["port"]),
            "PGUSER": self.connection["user"], "PGPASSWORD": self.connection["password"],
            "PGDATABASE": database, "PGSSLMODE": self.connection["sslmode"],
            "PGCONNECT_TIMEOUT": "10", "PGAPPNAME": "sunny-postgres-store",
        }
        env.update(variables)
        command = []
        if self.container:
            command = ["docker", "exec", "-i"]
            for key in variables:
                command += ["--env", key]  # Values are never placed in argv.
            command += [self.container]
        command += [program, *args]
        try:
            result = subprocess.run(
                command, env=env, stdin=stdin, stdout=stdout or subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=self.timeout, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise PostgresStoreError("postgres_tool_failed") from None
        if result.returncode:
            raise PostgresStoreError("postgres_tool_failed")
        return result.stdout

    def sql(self, database, statement):
        with tempfile.TemporaryFile() as source:
            source.write(statement.encode())
            source.seek(0)
            value = self.run("psql", ["-X", "--no-password", "--quiet", "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1"], database, stdin=source)
        try:
            return value.decode().strip()
        except UnicodeError:
            raise PostgresStoreError("postgres_output_invalid") from None

    def validate(self):
        if self._validated:
            return
        for program in ("pg_dump", "pg_restore", "psql"):
            value = self.run(program, ["--version"])
            if not re.search(rb"\(PostgreSQL\) 17\.6(?:\s|$)", value):
                raise PostgresStoreError("postgres_tools_version_requires_17_6")
        self._validated = True

    def server(self):
        self.validate()
        version = self.sql(self.connection["maintenance_database"], "SHOW server_version_num")
        if not version.isdigit() or int(version) // 10000 != 17 or int(version) > 170006:
            raise PostgresStoreError("postgres_server_version_unsupported")
        return int(version)

    def toc(self, source):
        source.seek(0)
        with tempfile.TemporaryFile() as output:
            self.run("pg_restore", ["--list"], stdin=source, stdout=output)
            if output.tell() > MAX_TOC:
                raise PostgresStoreError("archive_toc_limit")
            digest, _ = digest_file(output)
        source.seek(0)
        return digest


def backup(pg, databases, directory):
    databases = records(databases)
    version = pg.server()
    directory = Path(directory)
    try:
        directory.mkdir(mode=0o700)
    except OSError:
        raise PostgresStoreError("backup_exists_or_unavailable") from None
    manifest = {
        "format": "sunny-postgres-backup", "version": 1, "backup_id": uuid4().hex,
        "created_at": datetime.now(UTC).isoformat(), "consistency": "per_database_snapshot",
        "server_version_num": version, "tools_version": "17.6", "databases": [],
    }
    for record in databases:
        name = record["database"]
        actual_owner = pg.sql(pg.connection["maintenance_database"], "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='" + name + "'")
        if actual_owner != record["owner"]:
            raise PostgresStoreError("source_owner_mismatch")
        filename = record["service"] + ".dump"
        with private_open(directory / filename, "wb") as output:
            pg.run("pg_dump", ["--format=custom", "--no-owner", "--no-acl", "--no-password"], name, stdout=output)
            output.flush()
            os.fsync(output.fileno())
        with private_open(directory / filename) as archive:
            digest, size = digest_file(archive)
            toc = pg.toc(archive)
        manifest["databases"].append({**record, "file": filename, "sha256": digest, "size": size, "toc_sha256": toc})
    publish_json(directory / "manifest.json", manifest)
    return manifest


def verify(pg, directory):
    pg.validate()
    directory = Path(directory)
    try:
        meta = directory.lstat()
        if not stat.S_ISDIR(meta.st_mode) or meta.st_mode & 0o077 or meta.st_uid != os.getuid():
            raise PostgresStoreError("private_directory_required")
        manifest = private_json(directory / "manifest.json")
        expected = {"format", "version", "backup_id", "created_at", "consistency", "server_version_num", "tools_version", "databases"}
        if not isinstance(manifest, dict) or set(manifest) != expected:
            raise PostgresStoreError("backup_manifest_invalid")
        if manifest["format"] != "sunny-postgres-backup" or type(manifest["version"]) is not int or manifest["version"] != 1 or manifest["consistency"] != "per_database_snapshot" or manifest["tools_version"] != "17.6":
            raise PostgresStoreError("backup_manifest_invalid")
        if not isinstance(manifest["backup_id"], str) or not re.fullmatch("[0-9a-f]{32}", manifest["backup_id"]):
            raise PostgresStoreError("backup_manifest_invalid")
        if type(manifest["server_version_num"]) is not int or not 170000 <= manifest["server_version_num"] <= 170006:
            raise PostgresStoreError("backup_manifest_invalid")
        if not isinstance(manifest["created_at"], str) or len(manifest["created_at"]) > 40 or datetime.fromisoformat(manifest["created_at"]).utcoffset() is None:
            raise PostgresStoreError("backup_manifest_invalid")
        entries = manifest["databases"]
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_DATABASES:
            raise PostgresStoreError("backup_manifest_invalid")
        plain = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"service", "database", "owner", "connect_roles", "file", "sha256", "size", "toc_sha256"}:
                raise PostgresStoreError("backup_manifest_invalid")
            plain.append({key: entry[key] for key in ("service", "database", "owner", "connect_roles")})
        records(plain)
        expected_files = {"manifest.json"}
        for entry in entries:
            if entry["file"] != entry["service"] + ".dump" or type(entry["size"]) is not int or entry["size"] <= 0:
                raise PostgresStoreError("backup_manifest_invalid")
            for key in ("sha256", "toc_sha256"):
                if not isinstance(entry[key], str) or not DIGEST.fullmatch(entry[key]):
                    raise PostgresStoreError("backup_manifest_invalid")
            expected_files.add(entry["file"])
            with private_open(directory / entry["file"]) as archive:
                digest, size = digest_file(archive)
                if (digest, size) != (entry["sha256"], entry["size"]) or pg.toc(archive) != entry["toc_sha256"]:
                    raise PostgresStoreError("backup_integrity")
        if {path.name for path in directory.iterdir()} != expected_files:
            raise PostgresStoreError("backup_inventory_mismatch")
    except (OSError, ValueError, TypeError, KeyError):
        raise PostgresStoreError("backup_manifest_invalid") from None
    return manifest


def restore(pg, directory, targets, receipt):
    # Every archive is verified before even consulting the target server.
    manifest = verify(pg, directory)
    targets = records(targets)
    mappings = {target["service"]: target for target in targets}
    source_names = {entry["database"] for entry in manifest["databases"]}
    if set(mappings) != {entry["service"] for entry in manifest["databases"]}:
        raise PostgresStoreError("target_mapping_incomplete")
    if any(target["database"] in source_names | {"postgres", "template0", "template1", pg.connection["maintenance_database"]} for target in targets):
        raise PostgresStoreError("target_name_not_new")
    receipt = Path(receipt)
    if receipt.exists() or receipt.is_symlink():
        raise PostgresStoreError("receipt_exists")
    parent = receipt.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o077 or parent.st_uid != os.getuid():
        raise PostgresStoreError("private_receipt_directory_required")
    pg.server()
    maintenance = pg.connection["maintenance_database"]
    if pg.sql(maintenance, "SELECT rolsuper FROM pg_roles WHERE rolname=current_user") != "t":
        raise PostgresStoreError("restore_requires_isolated_administrator")
    for target in targets:
        if pg.sql(maintenance, "SELECT count(*) FROM pg_database WHERE datname='" + target["database"] + "'") != "0":
            raise PostgresStoreError("target_exists")
        for role in target["connect_roles"]:
            if pg.sql(maintenance, "SELECT count(*) FROM pg_roles WHERE rolcanlogin AND rolname='" + role + "'") != "1":
                raise PostgresStoreError("target_role_unavailable")
    # Each database is atomic. A failed later database does not roll back earlier
    # complete databases. Keep admission closed until the entire set has restored.
    for entry in manifest["databases"]:
        target = mappings[entry["service"]]
        name, owner = quote(target["database"]), quote(target["owner"])
        with private_open(Path(directory) / entry["file"]) as archive:
            # Check again using the very file descriptor supplied to pg_restore.
            if digest_file(archive) != (entry["sha256"], entry["size"]):
                raise PostgresStoreError("backup_integrity")
            pg.sql(maintenance, f"CREATE DATABASE {name} OWNER {owner} TEMPLATE template0 CONNECTION LIMIT 0")
            pg.sql(maintenance, f"REVOKE ALL ON DATABASE {name} FROM PUBLIC")
            pg.run("pg_restore", ["--dbname", target["database"], "--single-transaction", "--exit-on-error", "--no-owner", "--no-acl", "--no-password", "--role", target["owner"]], target["database"], stdin=archive, stdout=subprocess.DEVNULL)
    for target in targets:
        name = quote(target["database"])
        for role in target["connect_roles"]:
            pg.sql(maintenance, f"GRANT CONNECT ON DATABASE {name} TO {quote(role)}")
    # No application configuration is changed. Connection-limit changes are one
    # maintenance-DB transaction; prior SQL failure leaves all new DBs gated.
    pg.sql(maintenance, "BEGIN; " + " ".join(f"ALTER DATABASE {quote(target['database'])} CONNECTION LIMIT -1;" for target in targets) + " COMMIT;")
    result = {"format": "sunny-postgres-restore-receipt", "version": 1, "backup_id": manifest["backup_id"], "restored_at": datetime.now(UTC).isoformat(), "restored": len(targets), "targets": targets}
    publish_json(receipt, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "verify", "restore"))
    parser.add_argument("--config", type=Path, required=True, help="private JSON: connection and databases (source or new target mapping)")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--docker-container", help="explicit official PG17.6 tools container; no credentials in argv")
    parser.add_argument("--receipt", type=Path, help="new private completion receipt, required for restore")
    args = parser.parse_args()
    try:
        config = private_json(args.config)
        if not isinstance(config, dict) or set(config) != {"connection", "databases"}:
            raise PostgresStoreError("invalid_config")
        pg = PostgresTools(config["connection"], args.docker_container)
        if args.operation == "backup":
            result = backup(pg, config["databases"], args.directory)
            print(json.dumps({"status": "complete", "databases": len(result["databases"])}))
        elif args.operation == "verify":
            result = verify(pg, args.directory)
            print(json.dumps({"status": "verified", "databases": len(result["databases"])}))
        else:
            if args.receipt is None:
                raise PostgresStoreError("receipt_required")
            result = restore(pg, args.directory, config["databases"], args.receipt)
            print(json.dumps({"status": "complete", "restored": result["restored"]}))
    except (PostgresStoreError, OSError) as error:
        code = str(error) if isinstance(error, PostgresStoreError) else "local_io_failed"
        print(json.dumps({"status": "failed", "error": code}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())