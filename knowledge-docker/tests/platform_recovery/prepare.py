#!/usr/bin/env python3
"""Prepare one private, isolated recovery target. Never start or stop a container."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
from urllib.parse import quote

DOCKER_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_SOURCE = DOCKER_ROOT / ".local/acceptance-5add1c1af0/knowledge-docker"
SOURCE_PROJECT = "sunny-acceptance-5add1c1af0"
SERVICES = ("gateway", "iam", "auth", "channel", "knowledge", "ingest", "retrieval", "llm", "graphiti", "agent", "mcp")
DATABASES = tuple(s for s in SERVICES if s != "gateway") + ("keycloak", "temporal", "temporal_visibility")
PRIVATE_FILES = (
    ".local/service-public-keys.json", ".local/delegation/auth-private.pem",
    ".local/knowledge-realm.json", ".local/s3.json", ".local/test-env.json",
    ".local/git-graph-regression.json",
) + tuple(f".local/identities/{s}/private.pem" for s in SERVICES)


class RecoveryError(RuntimeError):
    pass


def write_private(path, value, *, replace=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise RecoveryError("unsafe_file")
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_TRUNC if replace else os.O_EXCL)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(value.encode() if isinstance(value, str) else value)
        output.flush()
        os.fsync(output.fileno())
    path.chmod(0o600)


def read_private(path, root):
    path, root = Path(path), Path(root)
    if not path.is_relative_to(root):
        raise RecoveryError("private_path_outside_source")
    for candidate in (path, *path.parents):
        if candidate == root.parent:
            break
        if candidate.is_symlink():
            raise RecoveryError("symlink_not_allowed")
    meta = path.stat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077 or meta.st_uid != os.getuid():
        raise RecoveryError("private_file_required")
    if meta.st_size > 4_000_000:
        raise RecoveryError("private_file_size_limit")
    return path.read_bytes()


def read_env(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values or "\0" in value:
            raise RecoveryError("invalid_environment")
        values[key] = value
    return values


def database_mapping(prefix):
    return [{"service":s, "database":prefix+"_"+s,
             "owner":"temporal" if s=="temporal_visibility" else s,
             "connect_roles":["temporal" if s=="temporal_visibility" else s]}
            for s in DATABASES]


def port_available(port):
    try:
        with socket.socket() as handle:
            handle.bind(("127.0.0.1",port))
        return True
    except OSError:
        return False


def connection(values):
    return {"host":"127.0.0.1","port":5432,"user":"postgres",
            "password":values["POSTGRES_PASSWORD"],"sslmode":"disable","maintenance_database":"postgres"}


def db_url(service, database, values, *, host="postgres", port=5432):
    role = "temporal" if service=="temporal_visibility" else service
    scheme = "postgresql" if service in {"iam","auth","channel"} else "postgresql+psycopg"
    return f"{scheme}://{role}:{quote(values['DB_PASSWORD_'+role.upper()],safe='')}@{host}:{port}/{database}"


def overlay(values, recovery_id, record):
    services = {}
    for service in DATABASES:
        if service in {"temporal","temporal_visibility","keycloak"}:
            continue
        database = ("projection_" if service in {"retrieval","graphiti"} else "recovery_")+service
        services[service] = {"environment":{"DATABASE_URL":db_url(service,database,values)}}
    services["keycloak"] = {"command":["start-dev"], "environment":{
        "KC_DB_URL":"jdbc:postgresql://postgres:5432/recovery_keycloak"}}
    services["temporal"] = {"environment":{"DBNAME":"recovery_temporal","VISIBILITY_DBNAME":"recovery_temporal_visibility"}}
    services["retrieval"]["environment"].update({
        "RETRIEVAL_ES_INDEX":"knowledge-recovery-"+recovery_id,
        "RETRIEVAL_EMBEDDING_CONFIGURATION_ID":record["models"]["embedding"]["configuration_id"],
        "RETRIEVAL_RERANK_CONFIGURATION_ID":record["models"]["rerank"]["configuration_id"],
        "RETRIEVAL_EMBEDDING_DIMENSIONS":"8"})
    services["graphiti"]["environment"]["GRAPHITI_NAMESPACE"]="recovery-"+recovery_id
    services["ingest"]["environment"]["INGEST_S3_BUCKET"]="knowledge-recovery-"+recovery_id
    services["llm"]["environment"]["LLM_ALLOW_HTTP_PROVIDERS"]="true"
    for service in ("ingest","retrieval","graphiti","agent","channel"):
        services[service+"-worker"]={"environment":dict(services[service]["environment"])}
    return {"services":services}


def prepare(source, destination, recovery_id, postgres_port, *, copy_public=True):
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if source != EXPECTED_SOURCE.absolute() or source.resolve() != source:
        raise RecoveryError("source_deployment_not_authorized")
    if destination.exists() or destination.is_symlink() or destination.is_relative_to(source):
        raise RecoveryError("fresh_target_directory_required")
    if not re.fullmatch(r"[0-9a-f]{32}",recovery_id):
        raise RecoveryError("invalid_recovery_id")
    if type(postgres_port) is not int or not 1024 <= postgres_port <= 65535 or postgres_port in {18180,15439,28181,25440}:
        raise RecoveryError("invalid_target_postgres_port")
    if not port_available(postgres_port):
        raise RecoveryError("target_postgres_port_in_use")
    read_private(source/".env",source)
    values = read_env(source/".env")
    if (values.get("COMPOSE_PROJECT_NAME"),values.get("PUBLIC_WEB_URL"),values.get("GATEWAY_PORT"),values.get("POSTGRES_PORT")) != (
        SOURCE_PROJECT,"http://localhost:28181","28181","25440"):
        raise RecoveryError("source_deployment_identity_mismatch")
    required = {"POSTGRES_PASSWORD","S3_ACCESS_KEY","S3_SECRET_KEY","CREDENTIAL_ENCRYPTION_KEY","AGENT_ENCRYPTION_KEY","CHANNEL_ENCRYPTION_KEY"}
    required |= {"DB_PASSWORD_"+s.upper() for s in DATABASES}
    if any(not values.get(key) for key in required):
        raise RecoveryError("source_secret_inventory_incomplete")
    contents = {relative:read_private(source/relative,source) for relative in PRIVATE_FILES}
    record = json.loads(contents[".local/git-graph-regression.json"])
    if record.get("state")!="ready" or record.get("model_kind")!="deterministic_protocol_simulation":
        raise RecoveryError("protocol_projection_configuration_required")
    for capability in ("embedding","rerank"):
        if not isinstance(record.get("models",{}).get(capability,{}).get("configuration_id"),str):
            raise RecoveryError("protocol_projection_configuration_required")
    # All input/private-file validation precedes destination creation.
    destination.mkdir(parents=True,mode=0o700)
    target=destination/"knowledge-docker";target.mkdir(mode=0o700)
    project="sunny-recovery-"+recovery_id[:12]
    result={"format":"sunny-platform-recovery-plan","version":1,"recovery_id":recovery_id,
            "source_project":SOURCE_PROJECT,"source_root":str(source),"target_project":project,
            "target_root":str(target),"public_url":"http://localhost:28181",
            "source_postgres_port":25440,"target_postgres_port":postgres_port,
            "database_count":13,"fresh_projection_databases":["projection_retrieval","projection_graphiti"],
            "target_es_index":"knowledge-recovery-"+recovery_id,"target_graph_namespace":"recovery-"+recovery_id,
            "target_bucket":"knowledge-recovery-"+recovery_id,"state":"prepared_only"}
    target_values=dict(values,COMPOSE_PROJECT_NAME=project,POSTGRES_PORT=str(postgres_port))
    write_private(target/".env","".join(k+"="+v+"\n" for k,v in target_values.items()))
    for relative, data in contents.items():
        write_private(target/relative,data)
    tests=json.loads(contents[".local/test-env.json"])
    tests["databases"]={s:db_url(s,"recovery_"+s,values,host="127.0.0.1",port=postgres_port).replace("postgresql+psycopg://","postgresql://") for s in DATABASES}
    write_private(target/".local/test-env.json",json.dumps(tests,indent=2),replace=True)
    write_private(target/"compose.recovery.json",json.dumps(overlay(values,recovery_id,record),indent=2))
    private=target/".local/recovery";private.mkdir(mode=0o700)
    for name,prefix in (("source-pg.json","knowledge"),("target-pg.json","recovery")):
        write_private(private/name,json.dumps({"connection":connection(values),"databases":database_mapping(prefix)},indent=2))
    role_sql=[]
    for service in DATABASES:
        escaped=values["DB_PASSWORD_"+service.upper()].replace("'","''")
        role_sql.append(f"CREATE ROLE {service} LOGIN PASSWORD '{escaped}';")
    for service in ("retrieval","graphiti"):
        role_sql += [f"CREATE DATABASE projection_{service} OWNER {service};",
                     f"REVOKE ALL ON DATABASE projection_{service} FROM PUBLIC;",
                     f"GRANT CONNECT ON DATABASE projection_{service} TO {service};"]
    write_private(target/".local/init-databases.sql","\n".join(role_sql)+"\n")
    write_private(private/"preserved-material.json",json.dumps({
        "files":{relative:hashlib.sha256(data).hexdigest() for relative,data in contents.items()},
        "environment_secret_names":sorted(required),"origin_migration":False,
        "note":"Hash only; original bytes remain in the private deployment copy. test-env DSNs are intentionally retargeted."},indent=2))
    write_private(private/"plan.json",json.dumps(result,indent=2))
    if copy_public:
        for name in ("compose.yaml","compose.graph-regression.yaml"):
            shutil.copyfile(source/name,target/name)
        for sub in ("scripts","tests/platform_recovery","tests/git_graph_http","tests/model_provider","tests/git_fixture"):
            origin=DOCKER_ROOT/sub
            shutil.copytree(origin,target/sub,ignore=shutil.ignore_patterns("__pycache__","*.pyc"),dirs_exist_ok=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root",type=Path,default=EXPECTED_SOURCE)
    parser.add_argument("--destination",type=Path,required=True)
    parser.add_argument("--recovery-id",required=True)
    parser.add_argument("--postgres-port",type=int,default=25441)
    args=parser.parse_args()
    try:
        result=prepare(args.source_root,args.destination,args.recovery_id,args.postgres_port)
        print(json.dumps({k:result[k] for k in ("state","recovery_id","target_project","database_count")}))
    except (RecoveryError,OSError,KeyError,ValueError):
        print(json.dumps({"state":"failed","error":"recovery_preparation_failed"}))
        return 1
    return 0


if __name__=="__main__":raise SystemExit(main())