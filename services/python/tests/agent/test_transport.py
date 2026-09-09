import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
import json
import socket
from types import SimpleNamespace

import httpx
from sqlalchemy import update
import uvicorn

from knowledge_platform.agent.models import Run, now
from knowledge_platform.agent.streaming import EventResponse

from .test_runtime import setup_run


class RoutingTransport(httpx.AsyncBaseTransport):
    def __init__(self,handler):
        self.mock=httpx.MockTransport(handler)
        self.http=httpx.AsyncHTTPTransport()

    async def handle_async_request(self,request):
        if request.url.host=='127.0.0.1':
            return await self.http.handle_async_request(request)
        return await self.mock.handle_async_request(request)

    async def aclose(self):
        await self.mock.aclose()
        await self.http.aclose()


@asynccontextmanager
async def slow_llm(api):
    started,closed=asyncio.Event(),asyncio.Event()
    connections=[]
    state=SimpleNamespace(chat_count=0,started=started,closed=closed)
    async def handle(reader,writer):
        connections.append(asyncio.current_task())
        try:
            header=await reader.readuntil(b'\r\n\r\n')
            headers={line.split(':',1)[0].lower():line.split(':',1)[1].strip() for line in header.decode().split('\r\n')[1:] if ':' in line}
            assert 'authorization' not in headers
            assert 'x-service-token' in headers
            if header.startswith(b'GET '):
                payload=json.dumps({'configuration_id':'model1','capability':'chat','state':'active','max_output_tokens':8192,'max_input_chars':131072}).encode()
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: '+str(len(payload)).encode()+b'\r\n\r\n'+payload)
                await writer.drain()
                return
            data=json.loads(await reader.readexactly(int(headers['content-length'])))
            assert data['stream'] is True and data['configuration_id']=='model1'
            state.chat_count+=1
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n')
            payload=('event: content_delta\ndata: '+json.dumps({'type':'content_delta','delta':'未完成输出'},ensure_ascii=False)+'\n\n').encode()
            for piece in [payload[:1],payload[1:-2],payload[-2:]]:
                writer.write(format(len(piece),'x').encode()+b'\r\n'+piece+b'\r\n')
                await writer.drain()
            started.set()
            await reader.read()
            closed.set()
        except (ConnectionError,asyncio.IncompleteReadError):
            closed.set()
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
    server=await asyncio.start_server(handle,'127.0.0.1',0)
    api.settings.llm_url='http://127.0.0.1:'+str(server.sockets[0].getsockname()[1])
    old=api.app.state.service.clients.client
    async with httpx.AsyncClient(transport=RoutingTransport(api.state.handle),trust_env=False) as http:
        api.app.state.service.clients.client=http
        try:
            yield state
        finally:
            api.app.state.service.clients.client=old
            server.close()
            await server.wait_closed()
            for task in connections:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*connections,return_exceptions=True)


async def test_actual_tcp_explicit_cancel_closes_upstream_and_erases_delegation(api):
    async with slow_llm(api) as provider:
        _,_,result,_=await setup_run(api)
        task=asyncio.create_task(api.app.state.worker.run_once())
        await asyncio.wait_for(provider.started.wait(),3)
        response=await api.client.post('/api/v1/runs/'+result.json()['id']+'/cancel',json={})
        assert response.json()['status']=='cancelled'
        await asyncio.wait_for(provider.closed.wait(),3)
        await asyncio.wait_for(task,3)
        async with api.database.session() as session:
            row=await session.get(Run,result.json()['id'])
            assert row.encrypted_token is None and row.answer is None
        assert provider.chat_count==1


async def test_actual_tcp_absolute_deadline_closes_slow_model_stream(api):
    async with slow_llm(api) as provider:
        _,_,result,_=await setup_run(api,config={'space_ids':['engineering'],'model_configuration_id':'model1','budget':{'seconds':1}})
        await asyncio.wait_for(api.app.state.worker.run_once(),5)
        await asyncio.wait_for(provider.closed.wait(),3)
        value=(await api.client.get('/api/v1/runs/'+result.json()['id'])).json()
        assert value['status']=='partial' and value['error_code']=='run_deadline'
        async with api.database.session() as session:
            assert (await session.get(Run,result.json()['id'])).encrypted_token is None


async def test_actual_tcp_worker_shutdown_closes_stream_and_restart_records_uncertain_model(api):
    async with slow_llm(api) as provider:
        _,_,result,_=await setup_run(api)
        task=asyncio.create_task(api.app.state.worker.run_once())
        await asyncio.wait_for(provider.started.wait(),3)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await asyncio.wait_for(provider.closed.wait(),3)
        async with api.database.session() as session, session.begin():
            row=await session.get(Run,result.json()['id'])
            assert row.status=='running' and row.encrypted_token
            await session.execute(update(Run).where(Run.id==row.id).values(lease_until=now()-timedelta(seconds=1)))
        await api.app.state.worker.run_once()
        value=(await api.client.get('/api/v1/runs/'+result.json()['id'])).json()
        assert value['status']=='partial' and value['error_code']=='model_interrupted'
        assert provider.chat_count==1


async def test_slow_sse_receiver_has_a_bounded_send_and_closes_only_its_iterator(api):
    closed=asyncio.Event()
    async def stream():
        try:
            yield 'event: queued\ndata: {}\n\n'
            await asyncio.sleep(30)
        finally:
            closed.set()
    async def blocked_send(message):
        if message['type']=='http.response.body':
            await asyncio.sleep(30)
    api.settings.agent_sse_send_timeout_seconds=.1
    response=EventResponse(stream(),settings=api.settings)
    await asyncio.wait_for(response.stream_response(blocked_send),1)
    assert closed.is_set()


async def test_actual_tcp_sse_disconnect_leaves_durable_run_queued(api):
    _,_,result,_=await setup_run(api)
    sock=socket.socket()
    sock.bind(('127.0.0.1',0))
    sock.listen(64)
    port=sock.getsockname()[1]
    config=uvicorn.Config(api.app,lifespan='off',access_log=False,log_config=None,log_level='critical',timeout_graceful_shutdown=1)
    server=uvicorn.Server(config)
    task=asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(3):
            while not server.started:
                await asyncio.sleep(.01)
        async with httpx.AsyncClient(trust_env=False) as client:
            async with client.stream('GET',f'http://127.0.0.1:{port}/api/v1/runs/'+result.json()['id']+'/events',headers=api.headers()) as response:
                assert response.status_code==200
                async for line in response.aiter_lines():
                    if line.startswith('event: queued'):
                        break
        async with api.database.session() as session:
            row=await session.get(Run,result.json()['id'])
            assert row.status=='queued' and row.encrypted_token
    finally:
        server.should_exit=True
        await asyncio.wait_for(task,4)
        sock.close()


async def test_agent_boundary_bounds_chunked_json_before_parsing(api):
    async def content():
        yield b'{"title":"'
        for _ in range(3):
            yield b'p'*1_000_000
        yield b'"}'
    response=await api.client.post('/api/v1/sessions',content=content(),headers={'Content-Type':'application/json'})
    assert response.status_code==413
    assert 'pppppp' not in response.text