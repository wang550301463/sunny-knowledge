"""Canonical-owned lifecycle metadata; every read traverses current provenance ACL."""

import hashlib
import json
from datetime import timedelta

from sqlalchemy import func, select

from .authorization import coherent_authorization
from .lifecycle import POLICY_VERSION, calculate, policy_payload, utc
from .models import AccessEvent, LifecycleHead, LifecyclePolicy, LifecycleSnapshot, now
from .schemas import KnowledgeError, PageContent


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class LifecycleMethods:
    """Mixed into KnowledgeService to share its transaction and authorization epoch."""

    async def _lifecycle_actor(self, token, *, web=False, worker_id=None):
        principal = await self.auth.resolve(token)
        self._observe_epoch(principal.auth_epoch)
        if getattr(principal, "channel_context", None) is not None:
            raise KnowledgeError(403, "forbidden", "Channel delegation cannot access lifecycle metadata")
        if web and ("user:" + principal.id) not in principal.subjects:
            raise KnowledgeError(403, "forbidden", "Personal usage requires a Web user")
        if worker_id is not None and (
            not worker_id or principal.id != worker_id
            or ("service:" + principal.id) not in principal.subjects
        ):
            raise KnowledgeError(403, "forbidden", "Lifecycle service account is required")
        return principal.id

    async def _lifecycle_policy(self):
        policy = await self.session.get(LifecyclePolicy, POLICY_VERSION)
        if policy is None or policy.definition != policy_payload():
            raise KnowledgeError(503, "lifecycle_policy_unavailable", "Lifecycle policy is unavailable")

    async def _lifecycle_context(self, token, page_id, revision_id=None, *, lock=False):
        page = await self._page(page_id, lock=lock)
        await self._page_permission(token, page)
        revision_id = revision_id or page.current_revision
        if revision_id is None:
            raise KnowledgeError(409, "unpublished_page", "A published revision is required")
        revision = await self._revision(page.id, revision_id)
        sources, inputs = await self._dependency_closure(token, revision)
        resources = (
            {(page.space_id, page.id)}
            | {(row.space_id, row.resource_id) for row in sources}
            | {(row.space_id, row.id) for row, _ in inputs}
        )
        clauses = []
        for space, resource in sorted(resources):
            policy = await self._policy(space, resource)
            if (
                not isinstance(policy.get("space_read_subjects"), list)
                or "resource_read_subjects" not in policy
                or (policy["resource_read_subjects"] is not None
                    and not isinstance(policy["resource_read_subjects"], list))
            ):
                raise KnowledgeError(503, "policy_unavailable", "Complete provenance policy is required")
            for clause in [policy["space_read_subjects"], policy["resource_read_subjects"]]:
                if clause is not None:
                    if any(not isinstance(subject, str) for subject in clause):
                        raise KnowledgeError(503, "policy_unavailable", "Invalid provenance policy")
                    clauses.append(tuple(sorted(set(clause))))
        domain = fingerprint({"space_id": page.space_id, "read_clauses": sorted(set(clauses))})
        return page, revision, {row.id: row for row in sources}, domain

    async def _personal_access(self, actor, revision_id, domain, instant):
        row = (await self.session.execute(select(
            func.count().filter(AccessEvent.created_at > instant - timedelta(days=7)),
            func.count().filter(AccessEvent.created_at > instant - timedelta(days=30)),
            func.max(AccessEvent.created_at),
        ).where(
            AccessEvent.actor_id == actor, AccessEvent.revision_id == revision_id,
            AccessEvent.acl_domain == domain, AccessEvent.created_at <= instant,
        ))).one()
        return {
            "scope": "mine_current_acl_domain",
            "mine_visits_7d": row[0],
            "mine_visits_30d": row[1],
            "mine_last_accessed_at": row[2].isoformat() if row[2] else None,
        }

    @coherent_authorization
    async def lifecycle_metrics(self, token, page_id, revision_id=None, as_of=None):
        actor = await self._lifecycle_actor(token)
        await self._lifecycle_policy()
        instant = utc(as_of or now())
        if instant > now():
            raise KnowledgeError(422, "future_lifecycle_time", "Lifecycle reads cannot use a future time")
        page, revision, snapshots, domain = await self._lifecycle_context(token, page_id, revision_id)
        result = {
            "page_id": page.id, "revision_id": revision.id, "policy_version": POLICY_VERSION,
            "as_of": instant.isoformat(),
            **calculate(PageContent.model_validate(revision.content), snapshots, instant,
                        is_current=page.current_revision == revision.id),
            "access": await self._personal_access(actor, revision.id, domain, instant),
        }
        await self._lifecycle_actor(token)
        return result

    @coherent_authorization
    async def record_access(self, token, caller, page_id, request):
        self.workload(caller, ("gateway",))
        actor = await self._lifecycle_actor(token, web=True)
        page, revision, _, domain = await self._lifecycle_context(token, page_id, request.revision_id)
        payload = {"page_id": page_id, **request.model_dump(mode="json")}
        operation = await self._operation(actor, "lifecycle.access", request.idempotency_key, payload)
        if operation:
            await self._lifecycle_actor(token, web=True)
            return operation.result
        event = AccessEvent(
            actor_id=actor, page_id=page.id, revision_id=revision.id, acl_domain=domain,
            idempotency_key=request.idempotency_key, request_hash=fingerprint(payload), created_at=now(),
        )
        self.session.add(event)
        await self.session.flush()
        result = {"id": event.id, "revision_id": event.revision_id, "recorded_at": event.created_at.isoformat()}
        await self._remember(actor, "lifecycle.access", request.idempotency_key, payload, result)
        await self._lifecycle_actor(token, web=True)
        return result

    async def _lifecycle_snapshot(self, token, page_id, revision_id, scheduled_at):
        page, revision, sources, _ = await self._lifecycle_context(token, page_id, revision_id, lock=True)
        # A delayed daily job cannot turn a revision first learned after that day into a
        # historical observation. It can be included by the next day's independent job.
        if revision.created_at > scheduled_at:
            return None
        values = calculate(PageContent.model_validate(revision.content), sources, scheduled_at,
                           is_current=True)
        # Currentness changes after publication and is always re-evaluated by GET.
        # It must not contaminate a deterministic immutable daily snapshot.
        values["validity"].pop("is_current")
        values["validity"].pop("eligible")
        snapshot = await self.session.scalar(select(LifecycleSnapshot).where(
            LifecycleSnapshot.revision_id == revision.id,
            LifecycleSnapshot.policy_version == POLICY_VERSION,
            LifecycleSnapshot.scheduled_at == scheduled_at,
        ))
        if snapshot is None:
            snapshot = LifecycleSnapshot(
                page_id=page.id, revision_id=revision.id, revision_number=revision.number,
                policy_version=POLICY_VERSION, scheduled_at=scheduled_at, metrics=values,
            )
            self.session.add(snapshot)
            await self.session.flush()
            await self._audit("lifecycle.materialized", await self._actor(token), page.id,
                              {"snapshot_id": snapshot.id, "revision_id": revision.id,
                               "policy_version": POLICY_VERSION, "scheduled_at": scheduled_at.isoformat()})
        elif snapshot.metrics != values:
            raise KnowledgeError(409, "lifecycle_snapshot_conflict", "An immutable lifecycle snapshot differs")
        head = await self.session.get(LifecycleHead, (page.id, POLICY_VERSION))
        if page.current_revision == revision.id and (
            head is None or (revision.number >= head.revision_number
                             and scheduled_at >= head.scheduled_at
                             and head.snapshot_id != snapshot.id)
        ):
            if head is None:
                self.session.add(LifecycleHead(page_id=page.id, policy_version=POLICY_VERSION,
                    snapshot_id=snapshot.id, revision_number=revision.number, scheduled_at=scheduled_at))
            else:
                head.snapshot_id, head.revision_number, head.scheduled_at = snapshot.id, revision.number, scheduled_at
        return {"page_id": page.id, "revision_id": revision.id, "snapshot_id": snapshot.id}

    @coherent_authorization
    async def reconcile_lifecycle(self, token, caller, request, worker_principal_id):
        self.workload(caller, ("knowledge",))
        actor = await self._lifecycle_actor(token, worker_id=worker_principal_id)
        await self._require(token, "read", request.space_id)
        await self._lifecycle_policy()
        if request.scheduled_at > utc(now()).replace(hour=0, minute=0, second=0, microsecond=0):
            raise KnowledgeError(422, "future_lifecycle_time", "A daily job cannot run ahead of server UTC")
        payload = {**request.model_dump(mode="json"), "policy_version": POLICY_VERSION}
        old = await self._operation(actor, "lifecycle.reconcile", request.idempotency_key, payload)
        if old:
            for item in old.result["items"]:
                page, _, _, _ = await self._lifecycle_context(token, item["page_id"], item["revision_id"])
                if page.space_id != request.space_id:
                    raise KnowledgeError(503, "lifecycle_snapshot_unavailable", "Snapshot scope is inconsistent")
            await self._lifecycle_actor(token, worker_id=worker_principal_id)
            return old.result
        pages = await self.list_pages(token, request.space_id, request.cursor, request.limit)
        result = {"items": [], "next_cursor": pages.get("next_cursor")}
        for page in pages["items"]:
            value = await self._lifecycle_snapshot(token, page["id"], page["current_revision"], request.scheduled_at)
            if value is not None:
                result["items"].append(value)
        await self._remember(actor, "lifecycle.reconcile", request.idempotency_key, payload, result)
        await self._lifecycle_actor(token, worker_id=worker_principal_id)
        return result
