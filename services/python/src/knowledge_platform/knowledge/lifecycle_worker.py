"""Read-only delegated Temporal maintenance worker; all persistence belongs to Knowledge HTTP."""

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import Field, ValidationError
from temporalio import activity
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.worker import Worker

from knowledge_platform.common.observability import trace_headers
from knowledge_platform.common.security import ServiceSecurity

from .lifecycle import POLICY_VERSION, LifecycleReconcile, utc
from .lifecycle_config import KnowledgeSettings
from .lifecycle_workflow import LifecycleWorkflow
from .schemas import Key, KnowledgeError, StrictModel


class SnapshotItem(StrictModel):
    page_id: Key
    revision_id: Key
    snapshot_id: Key


class SnapshotBatch(StrictModel):
    items: list[SnapshotItem] = Field(max_length=25)
    next_cursor: Key | None


class LifecycleClient:
    def __init__(self, settings, *, client=None, security=None):
        self.settings = settings
        self.security = security or ServiceSecurity.from_settings(settings)
        self.client = client or httpx.AsyncClient(timeout=settings.request_timeout,
                                                  follow_redirects=False, trust_env=False)
        self.owned = client is None

    async def close(self):
        if self.owned:
            await self.client.aclose()

    async def token(self):
        config = self.settings
        if not all((config.knowledge_lifecycle_oidc_token_url,
                    config.knowledge_lifecycle_oidc_client_id,
                    config.knowledge_lifecycle_oidc_client_secret,
                    config.knowledge_lifecycle_principal_id)):
            raise KnowledgeError(403, "lifecycle_worker_required", "Configure the lifecycle service account")
        try:
            response = await self.client.post(config.knowledge_lifecycle_oidc_token_url,
                data={"grant_type": "client_credentials", "scope": "knowledge:read",
                      "client_id": config.knowledge_lifecycle_oidc_client_id,
                      "client_secret": config.knowledge_lifecycle_oidc_client_secret},
                follow_redirects=False)
            if response.status_code != 200:
                raise KnowledgeError(403, "lifecycle_worker_required", "Lifecycle service account authentication failed")
            data = response.json()
            token = data.get("access_token")
            if not isinstance(token, str) or not token or data.get("token_type", "").lower() != "bearer":
                raise ValueError
            return token
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise KnowledgeError(503, "lifecycle_identity_unavailable", "Lifecycle identity provider unavailable") from None

    async def reconcile(self, body):
        request = LifecycleReconcile.model_validate(body)
        token = await self.token()
        try:
            response = await self.client.post(
                self.settings.knowledge_url.rstrip("/") + "/internal/v1/lifecycle/reconcile",
                headers={**trace_headers(), "X-Service-Token": self.security.issue("knowledge"),
                         "Authorization": "Bearer " + token},
                json=request.model_dump(mode="json"), follow_redirects=False,
            )
            if response.status_code != 200:
                status = response.status_code if response.status_code in {401, 403, 409, 422} else 503
                raise KnowledgeError(status, "lifecycle_reconcile_unavailable", "Lifecycle reconciliation was not accepted")
            result = SnapshotBatch.model_validate(response.json())
            if len(result.items) > request.limit or (result.next_cursor is not None and result.next_cursor == request.cursor):
                raise ValueError
            return result.model_dump(mode="json")
        except (httpx.HTTPError, ValueError, TypeError, ValidationError):
            raise KnowledgeError(503, "lifecycle_reconcile_unavailable", "Lifecycle reconciliation unavailable") from None


class LifecycleActivities:
    def __init__(self, client):
        self.client = client

    @activity.defn(name="knowledge.lifecycle.reconcile")
    async def reconcile(self, body: dict) -> dict:
        try:
            result = await self.client.reconcile(body)
            # Snapshot metadata stays in canonical PG; workflow history needs only its cursor.
            return {"next_cursor": result["next_cursor"]}
        except KnowledgeError as error:
            raise ApplicationError("Knowledge lifecycle operation unavailable",
                                   type="LifecycleUnavailable",
                                   non_retryable=error.status in {401, 403, 409, 422}) from None
        except Exception:  # noqa: BLE001 -- never put provider/source/credential details in Temporal
            raise ApplicationError("Knowledge lifecycle operation unavailable",
                                   type="LifecycleUnavailable") from None


class LifecycleDispatcher:
    def __init__(self, client, settings):
        self.client, self.settings = client, settings

    async def dispatch(self, instant=None):
        scheduled = utc(instant or datetime.now(UTC)).replace(hour=0, minute=0, second=0, microsecond=0)
        for space in sorted(set(self.settings.knowledge_lifecycle_space_ids)):
            job = {"policy_version": POLICY_VERSION, "space_id": space, "scheduled_at": scheduled.isoformat()}
            identity = hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()
            try:
                await self.client.start_workflow(LifecycleWorkflow.run, job,
                    id="knowledge-lifecycle:" + identity,
                    task_queue=self.settings.knowledge_lifecycle_task_queue,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                )
            except WorkflowAlreadyStartedError:
                pass


async def main():
    config = KnowledgeSettings.from_env("knowledge")
    if not config.knowledge_lifecycle_space_ids or not config.knowledge_lifecycle_principal_id:
        raise RuntimeError("Configure the lifecycle service account and explicit knowledge spaces")
    http = LifecycleClient(config)
    try:
        temporal = await Client.connect(config.knowledge_lifecycle_temporal_address,
                                        namespace=config.knowledge_lifecycle_temporal_namespace)
        activities = LifecycleActivities(http)
        dispatcher = LifecycleDispatcher(temporal, config)
        async with Worker(temporal, task_queue=config.knowledge_lifecycle_task_queue,
                          workflows=[LifecycleWorkflow], activities=[activities.reconcile],
                          max_concurrent_activities=2, max_concurrent_workflow_tasks=2,
                          graceful_shutdown_timeout=timedelta(seconds=5)):
            while True:
                try:
                    await dispatcher.dispatch()
                except Exception:  # noqa: BLE001 -- safe operator log, never upstream details
                    logging.getLogger(__name__).warning("Knowledge lifecycle dispatch unavailable")
                await asyncio.sleep(60)
    finally:
        await http.close()


if __name__ == "__main__":
    asyncio.run(main())
