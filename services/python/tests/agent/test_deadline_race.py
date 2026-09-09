"""Deterministic scheduling around real PG leases and a blocked TCP final stream."""
import asyncio
from datetime import timedelta

import pytest

from knowledge_platform.agent.models import Run, now
from knowledge_platform.agent.schemas import AgentError
from test_answer_tcp import final_provider, prefix
from test_runtime import setup_run


@pytest.mark.parametrize('after_deadline', ['current_lease', 'new_owner', 'explicit_cancel'])
async def test_heartbeat_deadline_closes_tcp_and_finishes_only_its_current_lease(api, monkeypatch, after_deadline):
    async with final_provider(api) as provider:
        _, _, created, _ = await setup_run(api, config={
            'space_ids':['engineering'], 'model_configuration_id':'model1',
            'budget':{'seconds':4},
        })
        run_id=created.json()['id']
        worker=api.app.state.worker
        finishing=asyncio.Event()
        original_finish=worker.safe_finish
        original_heartbeat=worker.store.heartbeat
        first=True
        async def paused_finish(*args, **kwargs):
            nonlocal first
            if first:
                first=False
                finishing.set()
                # Model process has closed upstream and entered finalization. A
                # deadline heartbeat wins now, as it can under normal scheduling.
                await asyncio.Event().wait()
            await asyncio.wait_for(provider.closed.wait(),3)
            return await original_finish(*args,**kwargs)
        async def ordered_heartbeat(*args):
            try:
                return await original_heartbeat(*args)
            except AgentError as exc:
                if exc.code!='run_deadline':raise
                await asyncio.wait_for(finishing.wait(),3)
                if after_deadline=='new_owner':
                    async with api.database.session() as session,session.begin():
                        row=await session.get(Run,run_id,with_for_update=True)
                        row.lease_token='new-owner'
                        row.lease_until=now()+timedelta(seconds=30)
                elif after_deadline=='explicit_cancel':
                    response=await api.client.post('/api/v1/runs/'+run_id+'/cancel',json={})
                    assert response.status_code==200
                raise
        monkeypatch.setattr(worker,'safe_finish',paused_finish)
        monkeypatch.setattr(worker.store,'heartbeat',ordered_heartbeat)
        task=asyncio.create_task(worker.run_once())
        try:
            current=await prefix(api,provider,run_id)
            await asyncio.wait_for(task,9)
            await asyncio.wait_for(provider.closed.wait(),3)
            async with api.database.session() as session:
                row=await session.get(Run,run_id)
                if after_deadline=='current_lease':
                    assert row.status=='partial'
                    assert row.error_code=='run_deadline'
                    assert row.answer==current['answer']
                    assert row.encrypted_token is None and row.lease_token is None
                elif after_deadline=='new_owner':
                    assert row.status=='running' and row.lease_token=='new-owner'
                    assert row.encrypted_token is not None
                else:
                    assert row.status=='cancelled' and row.error_code=='cancelled_by_user'
                    assert row.encrypted_token is None
        finally:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)