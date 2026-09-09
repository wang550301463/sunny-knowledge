"""Wiki input ACL is retained independently of the model's selected citations."""

import pytest

from .test_http import client_for
from .test_postgres import content, publish

pytestmark = pytest.mark.asyncio


async def private_wiki(store, *, page_id="page:private-input", source_snapshot=None):
    from knowledge_platform.knowledge.schemas import PageContent, PageCreate

    body = content(source_snapshot) if source_snapshot else PageContent(
        title="Private incident analysis", markdown="Private Wiki conclusion without raw citations."
    )
    async with store[0]() as knowledge:
        created = await knowledge.create_page("alice", PageCreate(id=page_id, space_id="finance", content=body))
    async with store[0]() as knowledge:
        revision = await knowledge.approve("reviewer", created["proposal"]["id"], "Reviewed private knowledge")
    store[1].denied.add(("bob", "read", page_id))
    store[1].policies[page_id] = {
        "space_id": "finance", "resource_id": page_id,
        "space_read_subjects": ["user:alice", "user:bob", "user:reviewer"],
        "resource_read_subjects": ["user:alice", "user:reviewer"],
        "acl_version": 2, "auth_epoch": store[1].epoch,
    }
    return {"page_id": page_id, "revision_id": revision["id"]}


@pytest.mark.parametrize("has_public_evidence", [False, True])
async def test_private_wiki_input_restricts_public_proposal_even_with_only_public_citation(store, has_public_evidence):
    target, snapshot = await publish(store)
    private = await private_wiki(store, source_snapshot=snapshot if has_public_evidence else None)
    async with client_for(store) as (client, headers):
        proposed = await client.post("/api/v1/pages/page:payments/proposals", headers=headers("agent"), json={
            "base_revision": target["id"], "content": content(snapshot, "Derived private conclusion").model_dump(mode="json"),
            "input_revisions": [private], "reason": "Agent observed the private Wiki", "idempotency_key": "derived",
        })
        assert proposed.status_code == 201, proposed.text
        proposal = proposed.json()
        assert proposal["input_revisions"] == [private]
        assert (await client.get("/api/v1/reviews/" + proposal["id"], headers=headers(actor="bob"))).status_code == 403
        assert all(item["id"] != proposal["id"] for item in (await client.get("/api/v1/reviews", headers=headers(actor="bob"))).json()["items"])
        denied = await client.post("/api/v1/reviews/" + proposal["id"] + "/approve", headers=headers(actor="bob"), json={"reason": "Public reviewer"})
        assert denied.status_code == 403
        approved = await client.post("/api/v1/reviews/" + proposal["id"] + "/approve", headers=headers(actor="reviewer"), json={"reason": "Authorized reviewer"})
        assert approved.status_code == 200, approved.text
        assert approved.json()["input_revisions"] == [private]
        assert (await client.get("/api/v1/pages/page:payments", headers=headers(actor="bob"))).status_code == 403
        assert (await client.get("/api/v1/pages/page:payments/revisions/" + approved.json()["id"], headers=headers(actor="bob"))).status_code == 403