"""Public source reads redact storage addresses; registration keeps its internal record."""

import hashlib

from fastapi.encoders import jsonable_encoder

from .test_http import client_for


async def test_real_pg_http_snapshot_public_read_and_internal_registration_have_distinct_shapes(store):
    service, auth, _ = store
    raw = "package demo\nfunc Read() {}\n"
    payload = {"source_id": "git:contract", "source_revision": "a" * 40,
               "resource_id": "source:contract", "space_id": "engineering", "path": "read.go", "kind": "code",
               "text": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest(), "object_key": "private/immutable/storage-location"}
    async with client_for(store) as (client, headers):
        created = await client.post("/internal/v1/sources/snapshots", headers=headers(caller="ingest"), json=payload)
        assert created.status_code == 201
        record = created.json()
        assert record["object_key"] == payload["object_key"]
        repeated = await client.post("/internal/v1/sources/snapshots", headers=headers(caller="ingest"), json=payload)
        assert repeated.status_code == 201 and repeated.json() == record
        expected = {key: value for key, value in record.items() if key != "object_key"}
        for caller in ("gateway", "agent", "mcp"):
            response = await client.get("/api/v1/source-snapshots/" + record["id"], headers=headers(caller=caller))
            assert response.status_code == 200
            assert response.json() == expected
            assert payload["object_key"] not in response.text
        async with service() as knowledge:
            assert jsonable_encoder(await knowledge.get_snapshot("alice", record["id"])) == expected
        listed = await client.get(f"/internal/v1/sources/{payload['source_id']}/revisions/{payload['source_revision']}", headers=headers(caller="ingest"))
        assert listed.status_code == 200 and listed.json() == {"items": [expected]}
        auth.denied.add(("alice", "read", payload["resource_id"]))
        auth.epoch += 1
        forbidden = await client.get("/api/v1/source-snapshots/" + record["id"], headers=headers())
        assert forbidden.status_code == 403
        assert raw not in forbidden.text and payload["object_key"] not in forbidden.text