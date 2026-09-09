"""Broker-bound channel conversations; no actor, scope or configuration from public JSON."""
from copy import deepcopy

from sqlalchemy import select, text

from .authorization import Guard
from .models import ChannelConversation, Run, Session
from .schemas import RunCreate, fail
from .service import digest
from .store import TERMINAL, finish


class ChannelService:
    def __init__(self, service):
        self.service = service

    async def create(self, token, body):
        service = self.service
        guard = await Guard.begin(service.auth, token, allow_channel=True)
        context = guard.message()
        async with service.db.session() as session, session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": "agent-channel:" + context["channel_id"] + ":" + context["conversation_key"]})
            mapping = await session.scalar(select(ChannelConversation).where(ChannelConversation.channel_id == context["channel_id"], ChannelConversation.conversation_key == context["conversation_key"]))
            if mapping:
                conversation = await service.own_session(session, guard, mapping.session_id, lock=True)
            else:
                conversation = Session(owner=guard.principal.id, title="渠道会话", channel=ChannelConversation(channel_id=context["channel_id"], conversation_key=context["conversation_key"], context=deepcopy(context)))
                session.add(conversation)
                await session.flush()
            session_id = conversation.id
            await guard.finish()
        request = RunCreate(agent_id=context["agent_id"], session_id=session_id, configuration_id=context["agent_configuration_id"], question=body.question, space_ids=sorted(context["space_ids"]), idempotency_key="channel:" + digest([context["channel_id"], context["message_id"]]))
        return await service.create_run(token, request, channel=context)

    async def control(self, token, *, clear=False):
        service = self.service
        guard = await Guard.begin(service.auth, token, allow_channel=True)
        context = guard.message()
        async with service.db.session() as session, session.begin():
            mapping = await session.scalar(select(ChannelConversation).where(ChannelConversation.channel_id == context["channel_id"], ChannelConversation.conversation_key == context["conversation_key"]))
            if mapping is None:
                await guard.finish()
                return {"cleared": True} if clear else {"cancelled": False}
            conversation = await session.get(Session, mapping.session_id, with_for_update=True)
            if conversation is None or conversation.owner != guard.principal.id:
                raise fail("not_found", "Channel conversation not found", 404)
            guard.bind_channel(mapping.context)
            active = list(await session.scalars(select(Run).where(Run.session_id == conversation.id, Run.status.in_(["queued", "running"])).with_for_update()))
            for row in active:
                guard.bind_channel(row.channel.context if row.channel else None, run_id=row.id)
                finish(session, row, "cancelled", "session_cleared" if clear else "cancelled_by_user")
            if clear:
                conversation.cleared = True
            await guard.finish()
            return {"cleared": True} if clear else {"cancelled": bool(active), **({"run_id": active[0].id} if active else {})}

    async def cancel(self, token, run_id):
        guard = await Guard.begin(self.service.auth, token, allow_channel=True)
        guard.message()
        async with self.service.db.session() as session, session.begin():
            row = await self.service.own_run(session, guard, run_id, lock=True)
            if row.status not in TERMINAL:
                finish(session, row, "cancelled", "cancelled_by_user")
            await guard.finish()
            return await self.service.run_view(guard, row)