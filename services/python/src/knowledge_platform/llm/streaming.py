"""Owned stream lifetimes: deadlines cover awaited work and the HTTP send boundary."""

import asyncio
import sys
from contextlib import asynccontextmanager

from anyio import CancelScope
from fastapi.responses import StreamingResponse


async def close_stream(iterator):
    with CancelScope(shield=True):
        async with asyncio.timeout(5):
            await iterator.aclose()


@asynccontextmanager
async def deadline_context(manager, deadline):
    # The timeout exits before yielding control to a consumer. It must never cancel
    # some unrelated await in the task consuming this generator.
    async with asyncio.timeout_at(deadline):
        value = await manager.__aenter__()
    try:
        yield value
    finally:
        exception = sys.exc_info()
        with CancelScope(shield=True):
            async with asyncio.timeout(5):
                await manager.__aexit__(*exception)


async def next_before_deadline(iterator, deadline):
    if asyncio.get_running_loop().time() >= deadline:
        raise TimeoutError
    async with asyncio.timeout_at(deadline):
        return await anext(iterator)


class OwnedStreamingResponse(StreamingResponse):
    def __init__(self, content, *, deadline, **kwargs):
        super().__init__(content, **kwargs)
        self.deadline = deadline

    async def stream_response(self, send):
        try:
            # This context owns both iteration and sending. A blocked TCP write
            # therefore reaches this finally even when the iterator is suspended.
            async with asyncio.timeout_at(self.deadline):
                await super().stream_response(send)
        except TimeoutError:
            # A slow peer cannot receive a reliable terminal SSE error. Closing
            # the body leaves an incomplete stream rather than a false completion.
            pass
        finally:
            await close_stream(self.body_iterator)