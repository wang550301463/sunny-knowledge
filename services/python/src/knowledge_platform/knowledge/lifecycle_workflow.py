"""Daily deterministic workflow. History contains IDs/cursors, never credentials or text."""

import hashlib
import json
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError


@workflow.defn
class LifecycleWorkflow:
    @workflow.run
    async def run(self, job: dict):
        if job.get("policy_version") != "freshness-v1":
            raise ApplicationError("Unknown lifecycle policy", non_retryable=True)
        cursor = job.get("cursor")
        seen = {cursor}
        for _ in range(100):
            identity = hashlib.sha256(json.dumps(
                [job["policy_version"], job["space_id"], job["scheduled_at"], cursor],
                separators=(",", ":"),
            ).encode()).hexdigest()
            result = await workflow.execute_activity(
                "knowledge.lifecycle.reconcile",
                {"space_id": job["space_id"], "scheduled_at": job["scheduled_at"],
                 "cursor": cursor, "limit": 25, "idempotency_key": "lifecycle-" + identity},
                start_to_close_timeout=timedelta(seconds=60),
                schedule_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2),
                                         maximum_interval=timedelta(seconds=30), maximum_attempts=5),
            )
            cursor = result["next_cursor"]
            if cursor is None:
                return {"completed": True}
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ApplicationError("Lifecycle cursor did not advance", non_retryable=True)
            seen.add(cursor)
        workflow.continue_as_new({**job, "cursor": cursor})
