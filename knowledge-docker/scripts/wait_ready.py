#!/usr/bin/env python3
"""Wait for the public Keycloak discovery contract, without credentials or side effects."""

import json
from pathlib import Path
import time

from oidc_login import http_client


def main():
    config = json.loads((Path(__file__).resolve().parents[1] / ".local/test-env.json").read_text())
    origin = config["public_url"].rstrip("/")
    issuer = origin + "/idp/realms/knowledge"
    deadline = time.monotonic() + 120
    with http_client(origin, timeout=3) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(issuer + "/.well-known/openid-configuration")
                if response.status_code == 200 and response.json().get("issuer") == issuer:
                    print("Public gateway and Keycloak discovery ready")
                    return
            except (OSError, ValueError):
                pass
            # Imported here to keep deployment-only network failures generic.
            except Exception as error:
                import httpx

                if not isinstance(error, httpx.HTTPError):
                    raise
            time.sleep(1)
    raise SystemExit("Gateway/Keycloak discovery did not become ready within 120 seconds")


if __name__ == "__main__":
    main()
