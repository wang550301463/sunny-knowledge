#!/usr/bin/env python3
"""Provision a local machine identity, without granting it any knowledge access."""

import hmac
import json
from pathlib import Path

import httpx
from oidc_login import http_client, login

ROOT = Path(__file__).resolve().parents[1]


def require(response, statuses=(200,)):
    if response.status_code not in statuses:
        raise RuntimeError(f"Worker provisioning request failed (HTTP {response.status_code})")
    return response.json() if response.content else None


def configure():
    values = dict(line.split("=", 1) for line in (ROOT / ".env").read_text().splitlines() if line and not line.startswith("#"))
    base = values["PUBLIC_WEB_URL"].rstrip("/")
    with http_client(base, timeout=15, follow_redirects=False) as client:
        admin_token = require(client.post(base + "/idp/realms/master/protocol/openid-connect/token", data={
            "grant_type": "password", "client_id": "admin-cli", "username": "bootstrap", "password": values["KEYCLOAK_ADMIN_PASSWORD"],
        }))["access_token"]
        headers = {"Authorization": "Bearer " + admin_token}
        api = base + "/idp/admin/realms/knowledge"
        found = require(client.get(api + "/clients", params={"clientId": values["INGEST_OIDC_CLIENT_ID"]}, headers=headers))
        if not found:
            require(client.post(api + "/clients", headers=headers, json={
                "clientId": values["INGEST_OIDC_CLIENT_ID"], "name": "Knowledge source synchronization",
                "protocol": "openid-connect", "enabled": True, "publicClient": False,
                "standardFlowEnabled": False, "directAccessGrantsEnabled": False,
                "serviceAccountsEnabled": True, "clientAuthenticatorType": "client-secret",
                "secret": values["INGEST_OIDC_CLIENT_SECRET"],
                "defaultClientScopes": ["basic", "knowledge:read", "knowledge:write"],
                "protocolMappers": [{"name": "knowledge-api-audience", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper", "config": {"included.custom.audience": "knowledge-api", "access.token.claim": "true", "id.token.claim": "false"}}],
            }), (201,))
            found = require(client.get(api + "/clients", params={"clientId": values["INGEST_OIDC_CLIENT_ID"]}, headers=headers))
        if len(found) != 1:
            raise RuntimeError("Worker client identity is ambiguous")
        account_client = found[0]
        if account_client.get("publicClient") or not account_client.get("serviceAccountsEnabled") or account_client.get("standardFlowEnabled") or account_client.get("directAccessGrantsEnabled"):
            raise RuntimeError("Existing worker client has unexpected authentication settings; no credentials overwritten")
        client_api = api + "/clients/" + account_client["id"]
        secret = require(client.get(client_api + "/client-secret", headers=headers))["value"]
        if not hmac.compare_digest(secret, values["INGEST_OIDC_CLIENT_SECRET"]):
            raise RuntimeError("Existing worker credential differs from local configuration; no credentials overwritten")
        worker = require(client.get(client_api + "/service-account-user", headers=headers))
        # Register the IAM account kind before its first machine token is ever resolved.
        user_tokens = login(base, values["BOOTSTRAP_USERNAME"], values["BOOTSTRAP_PASSWORD"])
        user_headers = {"Authorization": "Bearer " + user_tokens["access_token"]}
        accounts = require(client.get(base + "/api/v1/service-accounts", headers=user_headers))["items"]
        existing = next((account for account in accounts if account["id"] == worker["id"] or account.get("client_id") == values["INGEST_OIDC_CLIENT_ID"]), None)
        if existing is None:
            require(client.post(base + "/api/v1/service-accounts", headers=user_headers, json={"id": worker["id"], "name": "知识来源同步", "client_id": values["INGEST_OIDC_CLIENT_ID"]}), (201,))
        elif existing["id"] != worker["id"] or existing.get("client_id") != values["INGEST_OIDC_CLIENT_ID"] or not existing["active"]:
            raise RuntimeError("Existing worker binding is different or disabled; it was not modified")
        machine = require(client.post(base + "/idp/realms/knowledge/protocol/openid-connect/token", data={"grant_type": "client_credentials", "client_id": values["INGEST_OIDC_CLIENT_ID"], "client_secret": values["INGEST_OIDC_CLIENT_SECRET"]}))
        identity = require(client.get(base + "/api/v1/me", headers={"Authorization": "Bearer " + machine["access_token"]}))
        if identity["id"] != worker["id"] or "service:" + worker["id"] not in identity["subjects"] or identity["permissions"]:
            raise RuntimeError("Worker did not resolve as an unprivileged service principal")
        record = {"principal_id": worker["id"], "subject": "service:" + worker["id"], "client_id": values["INGEST_OIDC_CLIENT_ID"]}
        target = ROOT / ".local/ingest-worker.json"
        target.write_text(json.dumps(record, indent=2) + "\n")
        target.chmod(0o600)
        test_path = ROOT / ".local/test-env.json"
        test_config = json.loads(test_path.read_text())
        test_config["ingest_principal_id"] = worker["id"]
        test_path.write_text(json.dumps(test_config, indent=2) + "\n")
    print("Worker identity verified. No space/resource grants were added; credentials were not printed.")


if __name__ == "__main__":
    try:
        configure()
    except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as error:
        raise SystemExit(str(error) if isinstance(error, RuntimeError) else "Worker provisioning failed; check local configuration and service availability") from None
