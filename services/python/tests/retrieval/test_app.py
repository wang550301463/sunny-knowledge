from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException


class Security:
    def verify(self,token):
        if token not in {"gateway","agent","mcp","ingest","graphiti"}:
            raise HTTPException(401,"Invalid identity")
        return token


class Service:
    async def search(self,token,body):
        assert token=="delegated"
        return {"items":[],"gaps":["no_authorized_evidence"]}


@pytest.mark.asyncio
async def test_http_boundary_rejects_forged_identity_missing_bearer_and_unknown_inputs():
    from knowledge_platform.retrieval.app import create_app
    from knowledge_platform.retrieval.config import RetrievalSettings
    app=create_app(settings=RetrievalSettings(),runtime=Service(),security=Security())
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://retrieval") as client:
        payload={"query":"Pay","space_ids":["engineering"]}
        assert (await client.post('/api/v1/search',json=payload)).status_code==401
        assert (await client.post('/api/v1/search',headers={"X-Service-Token":"gateway"},json=payload)).status_code==401
        assert (await client.post('/api/v1/search',headers={"X-Service-Token":"ingest","Authorization":"Bearer delegated"},json=payload)).status_code==403
        headers={"X-Service-Token":"gateway","Authorization":"Bearer delegated"}
        response=await client.post('/api/v1/search',headers=headers,json=payload|{"principal":{"id":"admin"},"query":"SECRET"})
        assert response.status_code==422 and "SECRET" not in response.text
        assert (await client.post('/api/v1/search',headers=headers,json=payload)).status_code==200
        assert (await client.post('/internal/v1/search',headers=headers,json=payload)).status_code==403
        assert (await client.get('/metrics')).status_code==401