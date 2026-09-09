"""Short transactions with fencing. No HTTP awaits while holding a worker lease-row lock."""
from copy import deepcopy
from datetime import timedelta

from sqlalchemy import or_, select

from .models import Event, Run, new_id, now
from .schemas import fail

TERMINAL = {'completed', 'partial', 'failed', 'cancelled'}


def append_event(session, run, kind, data=None):
    run.event_seq += 1
    session.add(Event(run_id=run.id, seq=run.event_seq, type=kind, data=data or {}))


def finish(session, run, status, code=None, answer=None):
    run.status, run.error_code, run.answer = status, code, answer
    run.encrypted_token = None
    run.lease_token = None
    run.lease_until = None
    run.finished_at = now()
    # Completed public answers and dependency references are sufficient for history.
    # Intermediate model/tool messages need not be retained beyond terminal execution.
    run.checkpoint = {'rounds': run.checkpoint.get('rounds', 0), 'tool_calls': run.checkpoint.get('tool_calls', 0)}
    append_event(session, run, 'completed', {'status': status, 'error_code': code})


class Store:
    def __init__(self, database, settings):
        self.database, self.settings = database, settings

    async def claim(self):
        async with self.database.session() as session, session.begin():
            row = await session.scalar(select(Run).where(Run.status.in_(['queued','running']), or_(Run.lease_until.is_(None), Run.lease_until <= now())).order_by(Run.created_at, Run.id).limit(1).with_for_update(skip_locked=True))
            if row is None:
                return None
            if row.deadline <= now():
                finish(session, row, 'failed', 'delegation_expired')
                return {'expired': True}
            row.lease_token = new_id()
            row.lease_until = now() + timedelta(seconds=self.settings.agent_lease_seconds)
            row.status = 'running'
            append_event(session, row, 'started', {'resumed': bool(row.checkpoint)})
            await session.flush()
            return {key: deepcopy(getattr(row, key)) for key in ('id','owner','session_id','configuration_id','configuration','request','actual_scope','deadline','lease_token','checkpoint','dependencies','citations','usage','encrypted_token')}

    async def heartbeat(self, run_id, lease_token):
        async with self.database.session() as session, session.begin():
            row = await session.get(Run, run_id, with_for_update=True)
            self.fenced(row, lease_token)
            if row.deadline <= now():
                raise fail('run_deadline', 'Run deadline reached', 409)
            row.lease_until = now() + timedelta(seconds=self.settings.agent_lease_seconds)

    @staticmethod
    def fenced(row, lease_token):
        if row is None or row.status != 'running' or row.lease_token != lease_token or row.lease_until is None or row.lease_until <= now():
            raise fail('lease_lost', 'Run lease is no longer current', 409)

    async def save(self, run, event=None, event_data=None, terminal=None, code=None, answer=None):
        async with self.database.session() as session, session.begin():
            row = await session.get(Run, run['id'], with_for_update=True)
            self.fenced(row, run['lease_token'])
            for key in ['checkpoint','dependencies','citations','usage']:
                setattr(row, key, deepcopy(run[key]))
            if terminal:
                finish(session, row, terminal, code, answer)
            elif event:
                append_event(session, row, event, event_data)

    async def get(self, run_id):
        async with self.database.session() as session:
            return await session.get(Run, run_id)