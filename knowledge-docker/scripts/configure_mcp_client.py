#!/usr/bin/env python3
"""Pre-register an explicit MCP PKCE client without widening existing clients."""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from configure_worker import require
from oidc_login import http_client

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REDIRECTS = [
    "http://127.0.0.1:18791/callback",
    "http://localhost:18791/callback",
]


def secure_url(value, *, origin=False):
    parsed = urlsplit(value)
    if (
        not parsed.hostname or parsed.username or parsed.password
        or parsed.fragment or "*" in value or any(ord(c) < 33 for c in value)
        or parsed.scheme not in {"https", "http"}
        or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
        or (origin and (parsed.path not in {"", "/"} or parsed.query))
    ):
        raise ValueError("Use an exact HTTPS URL or local loopback HTTP URL")
    _ = parsed.port  # Reject malformed/out-of-range ports before registration.
    return value.rstrip("/") if origin else value


def client_definition(public_url, client_id, redirects):
    public_url = secure_url(public_url, origin=True)
    if not re.fullmatch(r"[a-zA-Z0-9._-]{1,100}", client_id):
        raise ValueError("Invalid client ID")
    if client_id in {"knowledge-web", "knowledge-cli"} or client_id.endswith("-worker"):
        raise ValueError("MCP needs a dedicated client ID")
    if not redirects or len(redirects) > 20 or len(set(redirects)) != len(redirects):
        raise ValueError("Specify 1 to 20 unique exact redirect URIs")
    return {
        "clientId": client_id, "name": "Knowledge MCP",
        "protocol": "openid-connect", "enabled": True, "publicClient": True,
        "standardFlowEnabled": True, "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False,
        "fullScopeAllowed": False,
        "redirectUris": [secure_url(uri) for uri in redirects], "webOrigins": [],
        "defaultClientScopes": ["basic", "knowledge:read"],
        "optionalClientScopes": ["knowledge:feedback"],
        "attributes": {"pkce.code.challenge.method": "S256"},
        "protocolMappers": [
            {
                "name": name, "protocol": "openid-connect",
                "protocolMapper": "oidc-audience-mapper",
                "config": {
                    "included.custom.audience": audience,
                    "id.token.claim": "false", "access.token.claim": "true",
                },
            }
            for name, audience in [
                ("knowledge-api-audience", "knowledge-api"),
                ("knowledge-mcp-audience", public_url + "/mcp"),
            ]
        ],
    }


def validate_existing(existing, expected):
    for key, value in expected.items():
        actual = existing.get(key)
        if key == "attributes":
            valid = isinstance(actual, dict) and actual.get("pkce.code.challenge.method") == "S256"
        elif key == "protocolMappers":
            normalize = lambda rows: sorted(
                json.dumps({k: v for k, v in row.items() if k != "id"}, sort_keys=True)
                for row in rows
            )
            valid = isinstance(actual, list) and normalize(actual) == normalize(value)
        elif isinstance(value, list):
            valid = isinstance(actual, list) and sorted(actual) == sorted(value)
        elif key == "name":
            continue  # An operator may rename the client without changing its authority.
        else:
            valid = actual == value
        if not valid:
            raise RuntimeError("Existing MCP client differs in " + key + "; no settings overwritten")


def configure(client_id="knowledge-mcp", redirects=None, *, root=ROOT):
    values = dict(
        line.split("=", 1) for line in (root / ".env").read_text().splitlines()
        if line and not line.startswith("#")
    )
    base = values["PUBLIC_WEB_URL"].rstrip("/")
    expected = client_definition(base, client_id, redirects or DEFAULT_REDIRECTS)
    with http_client(base, timeout=20, follow_redirects=False) as client:
        token = require(client.post(
            base + "/idp/realms/master/protocol/openid-connect/token",
            data={"grant_type": "password", "client_id": "admin-cli", "username": "bootstrap", "password": values["KEYCLOAK_ADMIN_PASSWORD"]},
        ))["access_token"]
        headers = {"Authorization": "Bearer " + token}
        api = base + "/idp/admin/realms/knowledge"
        scopes = require(client.get(api + "/client-scopes", headers=headers))
        if not {"basic", "knowledge:read", "knowledge:feedback"} <= {item["name"] for item in scopes}:
            raise RuntimeError("Required realm scopes missing; initialize Keycloak scopes first")
        found = require(client.get(api + "/clients", params={"clientId": client_id}, headers=headers))
        if not found:
            require(client.post(api + "/clients", json=expected, headers=headers), (201,))
            found = require(client.get(api + "/clients", params={"clientId": client_id}, headers=headers))
        if len(found) != 1:
            raise RuntimeError("MCP client identity is ambiguous")
        actual = require(client.get(api + "/clients/" + found[0]["id"], headers=headers))
        validate_existing(actual, expected)
    # Safe deployment guidance only: no secret or token is retained.
    record = {
        "client_id": client_id, "resource_url": base + "/mcp",
        "issuer": base + "/idp/realms/knowledge",
        "redirect_uris": expected["redirectUris"],
        "default_scopes": ["knowledge:read"], "optional_scopes": ["knowledge:feedback"],
    }
    path = root / ".local/mcp-client.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    path.chmod(0o600)
    print("Dedicated MCP PKCE client verified; Web, workers and knowledge grants unchanged.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", default="knowledge-mcp")
    parser.add_argument("--redirect-uri", action="append", dest="redirects")
    args = parser.parse_args()
    try:
        configure(args.client_id, args.redirects)
    except (httpx.HTTPError, ValueError, KeyError, RuntimeError) as error:
        raise SystemExit(str(error) if isinstance(error, (ValueError, RuntimeError)) else "MCP client provisioning failed; check local configuration and readiness") from None
