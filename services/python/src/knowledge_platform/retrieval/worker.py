"""Durable knowledge-outbox projection consumer, with continuous ACL reconciliation."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from knowledge_platform.common.auth import HTTPAuthorizer
from knowledge_platform.common.db import create_database

from .authorization import AuthorizationGuard
from .clients import Clients, MachineTokens
from .config import RetrievalSettings
from .elasticsearch import ElasticIndex
from .models import initialize
from .projection import compile_fragments
from .schemas import RetrievalError, Scope, unavailable
from .store import Catalog


class ProjectionWorker:
    def __init__(self,settings,authorizer,catalog,index,clients,tokens):
        self.settings,self.auth,self.catalog,self.index,self.clients,self.tokens=settings,authorizer,catalog,index,clients,tokens

    async def build(self,projection,existing):
        settings=self.settings
        fragments=compile_fragments(projection,configuration_id=settings.retrieval_embedding_configuration_id,dimensions=settings.retrieval_embedding_dimensions)
        previous={f.id:f for f in existing}
        pending=[]
        for fragment in fragments:
            old=previous.get(fragment.id)
            if old and old.text==fragment.text and old.embedding_configuration_id==fragment.embedding_configuration_id and old.embedding_dimensions==fragment.embedding_dimensions and old.embedding is not None:
                fragment.embedding=old.embedding
            elif fragment.state=="valid":
                pending.append(fragment)
        if not pending:
            return fragments
        token=await self.tokens.token()
        guard=await AuthorizationGuard.begin(self.auth,token,[projection["space_id"]])
        request=Scope(space_ids=[projection["space_id"]],include_historical=True,as_of=datetime.now(UTC))
        for offset in range(0,len(pending),16):
            batch=pending[offset:offset+16]
            allowed=await self.clients.pages(token,batch,request)
            await guard.finish()
            if allowed != {f.id for f in batch}:
                raise unavailable("worker_authorization_required", "The retrieval service account cannot read this revision")
            vectors=await self.clients.embeddings(token,settings.retrieval_embedding_configuration_id,[f.text for f in batch],settings.retrieval_embedding_dimensions)
            for fragment,vector in zip(batch,vectors,strict=True):
                fragment.embedding=vector
        allowed=await self.clients.pages(token,pending,request)
        await guard.finish()
        if allowed != {f.id for f in pending}:
            raise unavailable("worker_authorization_required", "The retrieval service account lost revision access")
        return fragments

    async def write(self,fragments,generation):
        await self.index.put(fragments,generation=generation)

    async def project(self,page_id,revision_id,event_id=None):
        async with asyncio.timeout(self.settings.retrieval_worker_timeout_seconds):
            projection=await self.clients.projection(page_id,revision_id)
            if projection.get("page_id")!=page_id or projection.get("revision_id")!=revision_id:
                raise unavailable("invalid_projection", "Canonical projection identity is inconsistent")
            return await self.catalog.project(projection,self.build,self.write,event_id=event_id)

    async def once(self):
        leased=await self.clients.call("POST","/internal/v1/outbox/lease","knowledge",body={"consumer":"retrieval","limit":1,"lease_seconds":300})
        try:
            items=leased["items"]
            if not isinstance(items,list) or len(items)>1:
                raise ValueError
            for event in items:
                await self.project(event["page_id"],event["revision_id"],event["id"])
                await self.clients.call("POST",f"/internal/v1/outbox/{quote(event['id'],safe='')}/ack","knowledge",body={"lease_token":event["lease_token"]})
        except (KeyError,TypeError,ValueError):
            raise unavailable("invalid_outbox_response", "Knowledge outbox response is invalid") from None
        return bool(items)

    async def reconcile(self):
        cutoff=datetime.now(UTC)-timedelta(seconds=self.settings.retrieval_reconcile_seconds)
        for row in await self.catalog.due(cutoff,limit=20):
            await self.project(row["page_id"],row["revision_id"])


async def run():
    settings=RetrievalSettings()
    database=create_database(settings.database_url)
    auth=HTTPAuthorizer(settings)
    index=ElasticIndex(settings)
    tokens=MachineTokens(settings)
    await initialize(database.engine)
    worker=ProjectionWorker(settings,auth,Catalog(database),index,Clients(settings,auth),tokens)
    try:
        while True:
            try:
                await index.initialize(settings.retrieval_embedding_dimensions,settings.retrieval_embedding_configuration_id)
                busy=await worker.once()
                await worker.reconcile()
                if not busy:
                    await asyncio.sleep(settings.retrieval_poll_seconds)
            except (RetrievalError,HTTPException,SQLAlchemyError,TimeoutError):
                # No IDs, source text, URLs, bearer tokens, or exception payloads enter logs.
                logging.warning("Retrieval projection attempt failed; delivery remains retryable")
                await asyncio.sleep(settings.retrieval_poll_seconds)
    finally:
        await tokens.close()
        await index.close()
        await auth.close()
        await database.close()


if __name__=="__main__":
    asyncio.run(run())