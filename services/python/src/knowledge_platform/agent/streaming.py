"""SSE readers consume durable events; disconnects do not own or cancel the run."""
import asyncio
import json

from anyio import CancelScope
from fastapi.responses import StreamingResponse

from .schemas import AgentError


async def event_stream(service,token,run_id,cursor):
    deadline = asyncio.get_running_loop().time()+service.settings.agent_sse_lifetime_seconds
    try:
        while asyncio.get_running_loop().time()<deadline:
            # The batch is reauthorized again on every reconnect/poll, not trusted from the initial
            # HTTP handshake. A final answer is dynamically materialized after full dependency checks.
            batch, terminal = await service.event_batch(token,run_id,cursor)
            for event in batch:
                # Batch items contain only safe stages until the fully checked terminal payload.
                yield 'id: '+str(event['seq'])+'\nevent: '+event['type']+'\ndata: '+json.dumps(event,ensure_ascii=False,separators=(',',':'))+'\n\n'
                cursor = event['seq']
            if terminal and len(batch)<50:
                return
            if not batch:
                yield ': keepalive\n\n'
                await asyncio.sleep(service.settings.agent_event_poll_seconds)
    except AgentError as exc:
        yield 'event: error\ndata: '+json.dumps({'error':{'code':exc.code,'message':'Run event authorization or dependency unavailable'}})+'\n\n'


class EventResponse(StreamingResponse):
    def __init__(self,content,*,settings):
        super().__init__(content,media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
        self.send_timeout = settings.agent_sse_send_timeout_seconds

    async def stream_response(self,send):
        async def bounded(message):
            async with asyncio.timeout(self.send_timeout):
                await send(message)
        try:
            await bounded({'type':'http.response.start','status':self.status_code,'headers':self.raw_headers})
            async for chunk in self.body_iterator:
                if isinstance(chunk,str):
                    chunk = chunk.encode()
                await bounded({'type':'http.response.body','body':chunk,'more_body':True})
            await bounded({'type':'http.response.body','body':b'','more_body':False})
        except TimeoutError:
            return
        finally:
            with CancelScope(shield=True):
                async with asyncio.timeout(5):
                    await self.body_iterator.aclose()