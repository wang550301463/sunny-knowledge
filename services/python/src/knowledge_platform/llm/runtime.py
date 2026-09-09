"""Per-model PER-REPLICA backpressure. A deployment with N workers has N times the cap."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from .schemas import LLMError


@dataclass
class Slots:
    running: int = 0
    waiting: int = 0
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)


class ModelLimiter:
    def __init__(self):
        self.models = {}

    @asynccontextmanager
    async def slot(self, model_id, concurrency, max_queue):
        state = self.models.setdefault(model_id, Slots())
        acquired = False
        async with state.condition:
            if state.running >= concurrency:
                if state.waiting >= max_queue:
                    raise LLMError(429, 'model_busy', 'Model queue is full')
                state.waiting += 1
                try:
                    await state.condition.wait_for(lambda: state.running < concurrency)
                finally:
                    state.waiting -= 1
            state.running += 1
            acquired = True
        try:
            yield
        finally:
            if acquired:
                async with state.condition:
                    state.running -= 1
                    state.condition.notify_all()