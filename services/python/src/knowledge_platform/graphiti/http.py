"""Bound request bytes even when a caller omits Content-Length or sends chunks."""
from fastapi.responses import JSONResponse


class BodyLimit:
    def __init__(self, app, maximum=262144):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in {'POST', 'PUT', 'PATCH'}:
            await self.app(scope, receive, send)
            return
        parts, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            data = message.get('body', b'')
            size += len(data)
            if size > self.maximum:
                await JSONResponse(status_code=413, content={'error':{'code':'request_too_large', 'message':'Graph request exceeds the byte limit'}})(scope, receive, send)
                return
            parts.append(data)
            if not message.get('more_body', False):
                break
        body = b''.join(parts)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type':'http.request', 'body':body, 'more_body':False}
            return await receive()
        await self.app(scope, replay, send)