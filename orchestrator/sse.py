import asyncio
import logging
from abc import ABC, abstractmethod
from typing import AsyncIterator, Dict, List
from models import SSEEvent

logger = logging.getLogger("orchestrator.sse")


class EventEmitter(ABC):
    @abstractmethod
    async def emit(self, run_id: str, event: SSEEvent):
        ...

    @abstractmethod
    async def subscribe(self, run_id: str) -> AsyncIterator[SSEEvent]:
        ...


class InMemoryEventEmitter(EventEmitter):
    def __init__(self):
        self._queues: Dict[str, asyncio.Queue] = {}
        self._buffers: Dict[str, List[SSEEvent]] = {}

    def register(self, run_id: str):
        if run_id not in self._buffers:
            self._buffers[run_id] = []
            logger.info("[run_id=%s] emitter registered", run_id)

    async def emit(self, run_id: str, event: SSEEvent):
        if run_id not in self._buffers:
            self._buffers[run_id] = []
        self._buffers[run_id].append(event)

        queue = self._queues.get(run_id)
        if queue is not None:
            await queue.put(event)

    async def subscribe(self, run_id: str) -> AsyncIterator[SSEEvent]:
        for event in self._buffers.get(run_id, []):
            yield event
            # Only terminate on error — keep alive through interpret→confirm→apply
            if event.type == "error":
                return

        queue: asyncio.Queue = asyncio.Queue()
        self._queues[run_id] = queue
        try:
            while True:
                event = await queue.get()
                yield event
                # Only terminate on error; let client close on terminal result
                if event.type == "error":
                    break
        finally:
            self._queues.pop(run_id, None)

    def has_run(self, run_id: str) -> bool:
        return run_id in self._buffers or run_id in self._queues


emitter = InMemoryEventEmitter()
