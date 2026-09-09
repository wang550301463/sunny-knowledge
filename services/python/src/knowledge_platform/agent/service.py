"""Agent and private-conversation domain service; knowledge mutations remain proposals."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json

import jwt
from sqlalchemy import func, select, text

from .authorization import Guard
from .models import Agent, Audit, Configuration, Event, Feedback, Run, Session, Summary, new_id, now
from .schemas import AgentConfig, AgentError, RunCreate, fail
from .store import TERMINAL, append_event, finish


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(value):
    return sha256(encoded(value).encode()).hexdigest()


def merge_dependencies(*groups):
    result = {}
    for group in groups:
        for dependency in group:
            key = (dependency['space_id'], dependency['page_id'], dependency['revision_id'], dependency.get('mode','current'), dependency.get('as_of'), dependency.get('inspection',False))
            if key not in result:
                result[key] = deepcopy(dependency)
            else:
                refs = {encoded(ref):ref for ref in result[key].get('evidence',[]) + dependency.get('evidence',[])}
                result[key]['evidence'] = list(refs.values())
    if len(result) > 200:
        raise fail('evidence_budget_reached', 'Run evidence dependency budget reached', 409)
    return list(result.values())


class AgentService:
    def __init__(self, settings, database, authorizer, clients, box):
        self.settings, self.db, self.auth, self.clients, self.box = settings, database, authorizer, clients, box

    async def configuration_access(self, guard, row, *, edit=False):
        if row is None:
            raise fail('not_found', 'Agent not found', 404)
        if row.created_by != guard.principal.id and (edit or row.published_configuration is None):
            raise fail('not_found', 'Agent not found', 404)
        await guard.require('read', row.owner_space_id)
        if edit:
            await guard.require('write', row.owner_space_id)

    async def config_view(self, session, guard, row, configuration_id=None):
        await self.configuration_access(guard, row)
        selected = configuration_id or (row.current_configuration if row.created_by == guard.principal.id else row.published_configuration)
        if row.created_by != guard.principal.id and selected != row.published_configuration:
            raise fail('not_found', 'Agent configuration not found', 404)
        config = await session.get(Configuration, selected)
        if config is None or config.agent_id != row.id:
            raise fail('not_found', 'Agent configuration not found', 404)
        payload = deepcopy(config.config)
        visible = []
        for space in payload['space_ids']:
            if await guard.allowed('read', space):
                visible.append(space)
        payload['space_ids'] = visible
        payload['tool_space_ids'] = {tool:[space for space in spaces if space in visible] for tool,spaces in payload['tool_space_ids'].items()}
        return {'id':row.id, 'configuration_id':config.id, 'version':config.version, 'name':config.name, 'description':config.description, 'owner_space_id':row.owner_space_id, 'created_by':row.created_by, 'shared':row.published_configuration is not None, 'published_configuration_id':row.published_configuration, 'config':payload, 'created_at':config.created_at.isoformat()}

    async def check_config(self, guard, config):
        for space in config.space_ids:
            await guard.require('read', space)
        if config.model_configuration_id:
            metadata = await self.clients.call('llm','GET','/internal/v1/models/configurations/'+config.model_configuration_id)
            if metadata.get('capability') != 'chat' or metadata.get('configuration_id') != config.model_configuration_id or metadata.get('state') != 'active':
                raise fail('model_configuration_required', 'Select an active explicit Chat configuration', 409)
        await guard.finish()

    async def create_agent(self, token, body):
        guard = await Guard.begin(self.auth, token, 'knowledge:write')
        await guard.require('read', body.owner_space_id)
        await guard.require('write', body.owner_space_id)
        await self.check_config(guard, body.config)
        async with self.db.session() as session, session.begin():
            aid, cid = new_id(), new_id()
            row = Agent(id=aid, owner_space_id=body.owner_space_id, created_by=guard.principal.id, current_configuration=cid)
            session.add(row)
            await session.flush()
            session.add(Configuration(id=cid, agent_id=aid, version=1, name=body.name, description=body.description, config=body.config.model_dump(mode='json'), created_by=guard.principal.id))
            session.add(Audit(agent_id=aid, actor=guard.principal.id, action='created', configuration_id=cid))
            await session.flush()
            value = await self.config_view(session, guard, row)
            await guard.finish()
            return value

    async def update_agent(self, token, agent_id, body):
        guard = await Guard.begin(self.auth, token, 'knowledge:write')
        async with self.db.session() as session, session.begin():
            row = await session.get(Agent, agent_id, with_for_update=True)
            await self.configuration_access(guard, row, edit=True)
            if row.current_configuration != body.base_configuration_id:
                raise fail('configuration_conflict', 'Agent configuration changed', 409)
            await self.check_config(guard, body.config)
            row.version += 1
            row.current_configuration = new_id()
            session.add(Configuration(id=row.current_configuration, agent_id=row.id, version=row.version, name=body.name, description=body.description, config=body.config.model_dump(mode='json'), created_by=guard.principal.id))
            session.add(Audit(agent_id=row.id, actor=guard.principal.id, action='updated', configuration_id=row.current_configuration))
            await session.flush()
            result = await self.config_view(session, guard, row)
            await guard.finish()
            return result

    async def publish(self, token, agent_id, body):
        guard = await Guard.begin(self.auth, token, 'knowledge:write')
        async with self.db.session() as session, session.begin():
            row = await session.get(Agent, agent_id, with_for_update=True)
            if row is None:
                raise fail('not_found', 'Agent not found', 404)
            await guard.require('read', row.owner_space_id)
            await guard.require('grant', row.owner_space_id)
            if row.current_configuration != body.base_configuration_id:
                raise fail('configuration_conflict', 'Agent configuration changed', 409)
            row.published_configuration = row.current_configuration if body.shared else None
            session.add(Audit(agent_id=row.id, actor=guard.principal.id, action='published' if body.shared else 'unpublished', configuration_id=row.current_configuration))
            # Authorized grant administrators may publish another editor's configuration.
            await guard.finish()
            return {'id':row.id, 'configuration_id':row.current_configuration, 'published_configuration_id':row.published_configuration, 'shared':body.shared}

    async def get_agent(self, token, agent_id, configuration_id=None):
        guard = await Guard.begin(self.auth, token)
        async with self.db.session() as session:
            value = await self.config_view(session, guard, await session.get(Agent, agent_id), configuration_id)
            await guard.finish()
            return value

    async def list_agents(self, token, cursor=None, limit=50):
        guard = await Guard.begin(self.auth, token)
        items, after = [], cursor or ''
        async with self.db.session() as session:
            while len(items) <= limit:
                rows = list(await session.scalars(select(Agent).where(Agent.id > after).order_by(Agent.id).limit(100)))
                if not rows:
                    break
                for row in rows:
                    after = row.id
                    if row.created_by != guard.principal.id and row.published_configuration is None:
                        continue
                    if not await guard.allowed('read', row.owner_space_id):
                        continue
                    items.append(await self.config_view(session, guard, row))
                    if len(items) > limit:
                        break
            await guard.finish()
        return {'items':items[:limit], 'next_cursor':items[limit-1]['id'] if len(items)>limit else None}

    async def versions(self, token, agent_id, cursor=None, limit=50):
        guard = await Guard.begin(self.auth, token)
        async with self.db.session() as session:
            row = await session.get(Agent, agent_id)
            await self.configuration_access(guard, row, edit=True)
            query = select(Configuration).where(Configuration.agent_id == agent_id)
            if cursor:
                query = query.where(Configuration.version < cursor)
            rows = list(await session.scalars(query.order_by(Configuration.version.desc()).limit(limit+1)))
            items = [await self.config_view(session, guard, row, c.id) for c in rows[:limit]]
            await guard.finish()
            return {'items':items, 'next_cursor':str(rows[limit-1].version) if len(rows)>limit else None}

    async def create_session(self, token, body):
        guard = await Guard.begin(self.auth, token)
        async with self.db.session() as session, session.begin():
            row = Session(owner=guard.principal.id, title=body.title)
            session.add(row)
            await session.flush()
            await guard.finish()
            return {'id':row.id, 'title':row.title, 'created_at':row.created_at.isoformat()}

    async def own_session(self, session, guard, session_id, *, lock=False):
        row = await session.get(Session, session_id, with_for_update=lock)
        if row is None or row.owner != guard.principal.id or row.cleared:
            raise fail('not_found', 'Session not found', 404)
        return row

    async def list_sessions(self, token, cursor=None, limit=50):
        guard = await Guard.begin(self.auth, token)
        async with self.db.session() as session:
            rows = list(await session.scalars(select(Session).where(Session.owner == guard.principal.id, Session.cleared.is_(False), Session.id > (cursor or '')).order_by(Session.id).limit(limit+1)))
            await guard.finish()
            return {'items':[{'id':r.id,'title':r.title,'created_at':r.created_at.isoformat()} for r in rows[:limit]], 'next_cursor':rows[limit-1].id if len(rows)>limit else None}

    async def clear_session(self, token, session_id):
        guard = await Guard.begin(self.auth, token)
        async with self.db.session() as session, session.begin():
            row = await self.own_session(session, guard, session_id, lock=True)
            active = list(await session.scalars(select(Run).where(Run.session_id == row.id, Run.status.in_(['queued','running'])).with_for_update()))
            for run in active:
                finish(session, run, 'cancelled', 'session_cleared')
            row.cleared = True
            await guard.finish()
            return {'id':row.id,'cleared':True}

    async def create_run(self, token, body):
        guard = await Guard.begin(self.auth, token)
        request = body.model_dump(mode='json')
        request_hash = digest(request)
        async with self.db.session() as session, session.begin():
            await session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'), {'key':'agent-run:'+guard.principal.id+':'+body.idempotency_key})
            await self.own_session(session, guard, body.session_id, lock=True)
            existing = await session.scalar(select(Run).where(Run.owner == guard.principal.id, Run.idempotency_key == body.idempotency_key))
            if existing:
                if existing.request_hash != request_hash:
                    raise fail('idempotency_conflict', 'Run request key was already used for different input', 409)
                return await self.run_view(guard, existing)
            agent = await session.get(Agent, body.agent_id)
            await self.configuration_access(guard, agent)
            selected = body.configuration_id or (agent.current_configuration if agent.created_by == guard.principal.id else agent.published_configuration)
            if agent.created_by != guard.principal.id and selected != agent.published_configuration:
                raise fail('not_found','Published configuration not found',404)
            version = await session.get(Configuration, selected)
            if version is None or version.agent_id != agent.id:
                raise fail('not_found','Configuration not found',404)
            config = AgentConfig.model_validate(version.config)
            if not config.model_configuration_id:
                raise fail('model_configuration_required','Select an explicit Chat model configuration before running',409)
            actual = []
            for space in sorted(set(body.space_ids) & set(config.space_ids)):
                if await guard.allowed('read',space):
                    actual.append(space)
            if not actual:
                raise fail('empty_scope','No currently authorized knowledge scope remains',403)
            metadata = await self.clients.call('llm','GET','/internal/v1/models/configurations/'+config.model_configuration_id)
            if metadata.get('capability') != 'chat' or metadata.get('state') != 'active' or metadata.get('configuration_id') != config.model_configuration_id:
                raise fail('model_configuration_required', 'An active Chat configuration is required',409)
            if await session.scalar(select(Run.id).where(Run.session_id == body.session_id, Run.status.in_(['queued','running']))):
                raise fail('session_busy','A run is already active in this session',409)
            try:
                # Auth has just verified this exact token. Unverified exp is ONLY an additional
                # upper bound on the delegated lifetime, never identity or authorization input.
                expires = jwt.decode(token, options={'verify_signature':False})['exp']
                if type(expires) not in (int,float):
                    raise ValueError
                deadline = min(now()+timedelta(seconds=config.budget.seconds), datetime.fromtimestamp(expires, UTC))
                if deadline <= now():
                    raise ValueError
            except (jwt.InvalidTokenError, KeyError, ValueError, OverflowError):
                raise fail('delegation_expired','A currently valid bounded OAuth delegation is required',401) from None
            run_id = new_id()
            run = Run(id=run_id, owner=guard.principal.id, session_id=body.session_id, agent_id=agent.id, configuration_id=version.id, configuration=config.model_dump(mode='json'), request=request, actual_scope=actual, idempotency_key=body.idempotency_key, request_hash=request_hash, deadline=deadline, encrypted_token=self.box.encrypt(token, 'agent-run:'+run_id), event_seq=0, dependencies=[], citations={}, checkpoint={}, usage={})
            session.add(run)
            await session.flush()
            append_event(session, run, 'queued')
            await guard.finish()
            return await self.run_view(guard, run)

    async def own_run(self, session, guard, run_id, *, lock=False):
        row = await session.get(Run, run_id, with_for_update=lock)
        if row is None or row.owner != guard.principal.id:
            raise fail('not_found','Run not found',404)
        await self.own_session(session, guard, row.session_id)
        return row

    async def run_view(self, guard, row):
        safe = {'id':row.id, 'session_id':row.session_id, 'status':row.status, 'event_seq':row.event_seq, 'created_at':row.created_at.isoformat(), 'finished_at':row.finished_at.isoformat() if row.finished_at else None}
        for space in row.actual_scope:
            if not await guard.allowed('read',space):
                await guard.finish()
                return {**safe, 'content_hidden':True}
        if not await guard.dependencies(self.clients,row.dependencies,display=True):
            return {**safe,'content_hidden':True}
        return {**safe,'content_hidden':False, 'agent_id':row.agent_id, 'configuration_id':row.configuration_id, 'actual_scope':row.actual_scope, 'question':row.request['question'], 'answer':row.answer, 'citations':list(row.citations.values()) if row.answer else [], 'usage':row.usage, 'error_code':row.error_code, 'budget':row.configuration['budget'], 'rounds':row.checkpoint.get('rounds',0), 'tool_calls':row.checkpoint.get('tool_calls',0)}

    async def get_run(self, token, run_id):
        guard = await Guard.begin(self.auth,token)
        async with self.db.session() as session:
            return await self.run_view(guard,await self.own_run(session,guard,run_id))

    async def history(self, token, session_id, cursor=None, limit=50):
        guard = await Guard.begin(self.auth,token)
        async with self.db.session() as session:
            await self.own_session(session,guard,session_id)
            rows = list(await session.scalars(select(Run).where(Run.session_id == session_id, Run.id > (cursor or '')).order_by(Run.id).limit(limit+1)))
            items = [await self.run_view(guard,row) for row in rows[:limit]]
            await guard.finish()
            return {'items':items,'next_cursor':rows[limit-1].id if len(rows)>limit else None}

    async def cancel(self, token, run_id):
        guard = await Guard.begin(self.auth,token)
        async with self.db.session() as session, session.begin():
            row = await self.own_run(session,guard,run_id,lock=True)
            if row.status not in TERMINAL:
                finish(session,row,'cancelled','cancelled_by_user')
            await guard.finish()
            return {'id':row.id,'status':row.status}

    async def retry(self,token,run_id,body):
        guard = await Guard.begin(self.auth,token)
        async with self.db.session() as session:
            row = await self.own_run(session,guard,run_id)
            if row.status not in TERMINAL:
                raise fail('run_not_finished','Cancel or finish the previous run before retrying',409)
            if (await self.run_view(guard,row)).get('content_hidden'):
                raise fail('forbidden','Original run dependencies are no longer authorized',403)
            request = {**row.request,'configuration_id':row.configuration_id,'idempotency_key':body.idempotency_key}
        return await self.create_run(token,RunCreate.model_validate(request))

    async def feedback(self,token,body):
        guard = await Guard.begin(self.auth,token,'knowledge:feedback')
        async with self.db.session() as session, session.begin():
            row = await self.own_run(session,guard,body.run_id)
            if (await self.run_view(guard,row)).get('content_hidden'):
                raise fail('forbidden','Feedback target is no longer authorized',403)
            fingerprint = digest(body.model_dump(mode='json'))
            await session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'), {'key':'agent-feedback:'+guard.principal.id+':'+body.idempotency_key})
            old = await session.scalar(select(Feedback).where(Feedback.owner == guard.principal.id, Feedback.idempotency_key == body.idempotency_key))
            if old:
                if old.request_hash != fingerprint:
                    raise fail('idempotency_conflict','Feedback key has different input',409)
                return {'id':old.id,'run_id':old.run_id}
            record = Feedback(owner=guard.principal.id,run_id=row.id,rating=body.rating,comment=body.comment,idempotency_key=body.idempotency_key,request_hash=fingerprint)
            session.add(record)
            await session.flush()
            await guard.finish()
            return {'id':record.id,'run_id':row.id}

    async def event_batch(self,token,run_id,cursor):
        guard = await Guard.begin(self.auth,token)
        async with self.db.session() as session:
            row = await self.own_run(session,guard,run_id)
            value = await self.run_view(guard,row)
            if value['content_hidden']:
                raise fail('evidence_unavailable','Run content is no longer authorized',403)
            if cursor > row.event_seq:
                raise fail('invalid_event_cursor','Cursor is ahead of this run',409)
            events = list(await session.scalars(select(Event).where(Event.run_id == run_id, Event.seq > cursor).order_by(Event.seq).limit(50)))
            await guard.finish()
            return [{'seq':event.seq,'type':event.type,'data':{**event.data, **({'run':value} if event.type=='completed' else {})}} for event in events], row.status in TERMINAL

    async def export(self,token,run_id):
        value = await self.get_run(token,run_id)
        if value['content_hidden']:
            raise fail('evidence_unavailable','Run content is no longer authorized',403)
        if not value['answer']:
            raise fail('answer_unavailable','No completed answer to export',409)
        # Render plain generated text. Citation URLs are assembled from authorized references.
        def escape(value):
            return value.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        lines = ['# '+escape(value['question'])]
        for section,title in [('facts','事实'),('inferences','推断')]:
            lines += ['', '## '+title]
            for claim in value['answer'][section]:
                lines.append('- '+escape(claim['text'])+' '+ ' '.join('[^'+cid+']' for cid in claim['citation_ids']))
        lines += ['', '## 证据缺口'] + ['- '+escape(gap) for gap in value['answer']['gaps']]
        for citation in value['citations']:
            lines += ['', '[^'+citation['id']+']: '+escape(citation['evidence']['path'])+' '+citation['url']]
        return '\n'.join(lines)+'\n'

    async def summaries(self,token,session_id,create=False):
        guard = await Guard.begin(self.auth,token)
        async with self.db.session() as session, session.begin():
            await self.own_session(session,guard,session_id,lock=create)
            if create:
                rows = list(await session.scalars(select(Run).where(Run.session_id == session_id, Run.status.in_(['completed','partial']), Run.answer.is_not(None)).order_by(Run.created_at.desc(),Run.id.desc()).limit(12)))
                content, dependencies, ids = [], [], []
                for row in reversed(rows):
                    value = await self.run_view(guard,row)
                    if not value['content_hidden']:
                        content.append({'run_id':row.id,'answer':row.answer,'citations':value['citations']})
                        dependencies = merge_dependencies(dependencies,row.dependencies)
                        ids.append(row.id)
                if not content:
                    raise fail('summary_unavailable','No authorized completed answer is available',409)
                fingerprint = digest({'runs':ids,'content':content})
                old = await session.scalar(select(Summary).where(Summary.session_id == session_id, Summary.source_hash == fingerprint))
                if old:
                    return await self.summary_view(guard,old)
                version = (await session.scalar(select(func.max(Summary.version)).where(Summary.session_id == session_id)) or 0)+1
                row = Summary(session_id=session_id,version=version,run_ids=ids,content=content,dependencies=dependencies,source_hash=fingerprint)
                session.add(row)
                await session.flush()
                result = await self.summary_view(guard,row)
                await guard.finish()
                return result
            rows = list(await session.scalars(select(Summary).where(Summary.session_id == session_id).order_by(Summary.version.desc()).limit(50)))
            result = [await self.summary_view(guard,row) for row in rows]
            await guard.finish()
            return {'items':result}

    async def summary_view(self,guard,row):
        base = {'id':row.id,'version':row.version,'created_at':row.created_at.isoformat(),'independent_evidence_count':0}
        if not await guard.dependencies(self.clients,row.dependencies,display=True):
            return {**base,'content_hidden':True}
        return {**base,'content_hidden':False,'run_ids':row.run_ids,'content':row.content}

    async def previous_context(self,token,run):
        guard = await Guard.begin(self.auth,token,owner=run['owner'])
        content, dependencies = [], []
        async with self.db.session() as session:
            await self.own_session(session,guard,run['session_id'])
            current = await session.get(Run,run['id'])
            rows = list(await session.scalars(select(Run).where(Run.session_id == run['session_id'], Run.status.in_(['completed','partial']), Run.answer.is_not(None), Run.created_at < current.created_at).order_by(Run.created_at.desc()).limit(6)))
            for row in reversed(rows):
                if not set(row.actual_scope) <= set(run['actual_scope']):
                    continue
                value = await self.run_view(guard,row)
                if value['content_hidden']:
                    continue
                content.append({'question':row.request['question'],'historical_answer':row.answer})
                inherited = deepcopy(row.dependencies)
                for dependency in inherited:
                    dependency['mode'], dependency['inspection'] = 'historical', True
                dependencies = merge_dependencies(dependencies,inherited)
        await guard.finish()
        return content, dependencies