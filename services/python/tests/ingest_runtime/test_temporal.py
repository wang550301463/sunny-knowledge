from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from knowledge_platform.ingest.workflow import IngestWorkflow, TemporalDispatcher

async def test_dispatcher_starts_workflow_with_only_task_id_and_deterministic_identity():
    client=AsyncMock()
    dispatcher=TemporalDispatcher(None,client,'ingest-test')
    await dispatcher.start('task-uuid')
    args,kwargs=client.start_workflow.call_args
    assert args[1]=='task-uuid'
    assert kwargs['id']=='ingest:task-uuid'
    assert kwargs['task_queue']=='ingest-test'
    assert 'Bearer' not in str(client.start_workflow.call_args)