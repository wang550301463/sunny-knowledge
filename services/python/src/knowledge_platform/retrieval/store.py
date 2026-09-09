"""Per-page serialization plus monotonic external generations across failed transactions."""
from __future__ import annotations

import hashlib

from sqlalchemy import select, text

from .models import Delivery, Page, Revision, now
from .projection import checked_policies,policy_fingerprint
from .schemas import Fragment, unavailable


def lock_id(page_id):
    return int.from_bytes(hashlib.sha256(("retrieval:"+page_id).encode()).digest()[:8],signed=True)


class Catalog:
    def __init__(self,database):
        self.database=database

    async def policies(self,spaces,limit):
        async with self.database.session() as session:
            rows=(await session.execute(select(Revision.acl_domain,Revision.policies).where(Revision.space_id.in_(spaces)).distinct().limit(limit+1))).all()
            if len(rows)>limit:
                raise unavailable("projection_scope_too_large", "The requested scope exceeds the configured policy registry budget")
            return [{"acl_domain":r.acl_domain,"policies":r.policies} for r in rows]

    async def revisions(self,page_id):
        async with self.database.session() as session:
            rows=(await session.scalars(select(Revision).where(Revision.page_id==page_id).order_by(Revision.version))).all()
            return [{"page_id":r.page_id,"revision_id":r.revision_id,"version":r.version,"is_current":r.is_current,"generation":r.generation} for r in rows]

    async def due(self,cutoff,limit=20):
        async with self.database.session() as session:
            rows=(await session.execute(select(Revision.page_id,Revision.revision_id).where(Revision.checked_at<cutoff).order_by(Revision.checked_at,Revision.revision_id).limit(limit))).all()
            return [{"page_id":r.page_id,"revision_id":r.revision_id} for r in rows]

    async def project(self,projection,build,write,*,event_id=None):
        policies=checked_policies(projection)
        async with self.database.session() as session,session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"),{"key":lock_id(projection["page_id"])})
            if event_id and await session.get(Delivery,event_id):
                return "already_projected"
            page=await session.get(Page,projection["page_id"])
            if page is None:
                page=Page(page_id=projection["page_id"],current_revision=projection["current_revision"],current_version=projection["current_version"])
                session.add(page)
            elif projection["current_version"] > page.current_version:
                page.current_revision=projection["current_revision"]
                page.current_version=projection["current_version"]
            elif projection["current_version"]==page.current_version and projection["current_revision"]!=page.current_revision:
                raise unavailable("canonical_version_conflict", "Canonical current revision is inconsistent")
            row=await session.get(Revision,projection["revision_id"])
            if row and (row.page_id!=projection["page_id"] or row.version!=projection["version"]):
                raise unavailable("canonical_version_conflict", "Immutable revision identity changed")
            if row and row.acl_epoch>policies[0].auth_epoch:
                return "older_policy_skipped"
            # Sequence allocation is deliberately not rolled back. An HTTP write with an uncertain
            # result can never regain the same external version after a database transaction fails.
            generation=await session.scalar(text("SELECT nextval('retrieval_projection_generation')"))
            fs=await build(projection,[Fragment.model_validate(f) for f in row.fragments] if row else [])
            for f in fs:
                f.is_current=page.current_revision==f.revision_id
            previous=(await session.scalars(select(Revision).where(Revision.page_id==page.page_id,Revision.is_current.is_(True),Revision.revision_id!=page.current_revision))).all()
            for old in previous:
                old_fs=[Fragment.model_validate(f) for f in old.fragments]
                for f in old_fs:f.is_current=False
                await write(old_fs,generation)
                old.fragments=[f.model_dump(mode="json") for f in old_fs]
                old.is_current=False
                old.generation=generation
            await write(fs,generation)
            values=dict(page_id=projection["page_id"],version=projection["version"],space_id=projection["space_id"],acl_domain=projection["acl_domain"],policy_fingerprint=policy_fingerprint(policies),acl_epoch=policies[0].auth_epoch,policies=projection["policies"],fragments=[f.model_dump(mode="json") for f in fs],is_current=page.current_revision==projection["revision_id"],generation=generation,checked_at=now())
            if row is None:
                session.add(Revision(revision_id=projection["revision_id"],**values))
            else:
                for key,value in values.items():setattr(row,key,value)
            if event_id:
                session.add(Delivery(event_id=event_id,page_id=projection["page_id"],revision_id=projection["revision_id"],generation=generation))
            await session.flush()
            return "projected"