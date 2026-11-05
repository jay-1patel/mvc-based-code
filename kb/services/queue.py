import asyncio
import time
from typing import Any, Callable, Dict
from dataclasses import dataclass, field

from routing.config import logger


@dataclass
class QueueJob:
    job_id: str
    task: str
    payload: Dict
    priority: int = 5
    created_at: float = field(default_factory=time.time)
    attempts: int = 0
    max_retries: int = 3
    status: str = "pending"
    result: Any = None
    error: str = ""


class MessageQueue:
    def __init__(self, max_workers: int = 5, max_size: int = 1000):
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_size)
        self._handlers: Dict[str, Callable] = {}
        self._workers: list = []
        self._max_workers = max_workers
        self._running = False
        self._processed = 0
        self._failed = 0
        self._pending = 0

    def register_handler(self, task_name: str, handler: Callable):
        self._handlers[task_name] = handler
        logger.info(f"Queue handler registered: {task_name}")

    async def enqueue(
        self,
        task: str,
        payload: Dict,
        priority: int = 5,
        job_id: str = None,
    ) -> str:
        if not job_id:
            job_id = f"job_{int(time.time() * 1000)}_{self._pending}"

        job = QueueJob(
            job_id=job_id,
            task=task,
            payload=payload,
            priority=priority,
        )

        await self._queue.put((priority, time.time(), job))
        self._pending += 1
        logger.debug(f"Job enqueued: {job_id} task={task} priority={priority}")
        return job_id

    async def _process_job(self, job: QueueJob):
        handler = self._handlers.get(job.task)
        if not handler:
            logger.error(f"No handler for task: {job.task}")
            self._failed += 1
            return

        job.status = "processing"
        job.attempts += 1

        try:
            if asyncio.iscoroutinefunction(handler):
                result = await handler(job.payload)
            else:
                result = await asyncio.to_thread(handler, job.payload)
            job.status = "completed"
            job.result = result
            self._processed += 1
            logger.debug(f"Job completed: {job.job_id}")
        except Exception as e:
            job.error = str(e)
            self._failed += 1
            if job.attempts < job.max_retries:
                logger.warning(f"Job {job.job_id} failed (attempt {job.attempts}/{job.max_retries}): {e}")
                await asyncio.sleep(2 ** job.attempts)
                await self._queue.put((job.priority, time.time(), job))
            else:
                logger.error(f"Job {job.job_id} failed permanently: {e}")

    async def _worker(self, worker_id: int):
        logger.info(f"Queue worker {worker_id} started")
        while self._running:
            try:
                priority, timestamp, job = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
                self._pending = max(0, self._pending - 1)
                await self._process_job(job)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
                await asyncio.sleep(1)

    async def start(self):
        if self._running:
            return
        self._running = True
        for i in range(self._max_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        logger.info(f"MessageQueue started with {self._max_workers} workers")

    async def stop(self):
        self._running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("MessageQueue stopped")

    def get_stats(self) -> Dict:
        return {
            "running": self._running,
            "queue_size": self._queue.qsize(),
            "pending": self._pending,
            "processed": self._processed,
            "failed": self._failed,
            "workers": self._max_workers,
            "handlers": list(self._handlers.keys()),
        }


queue = MessageQueue(max_workers=5)


async def _handle_webhook_message(payload: Dict):
    from .kb_handler import handle_kb_query
    wa_id = payload.get("wa_id", "")
    message = payload.get("message", "")
    sender_name = payload.get("sender_name", "")
    await handle_kb_query(wa_id=wa_id, message=message, sender_name=sender_name)


async def _handle_send_message(payload: Dict):
    from .whatsapp import send_whatsapp_message
    to = payload.get("to", "")
    text = payload.get("text", "")
    send_whatsapp_message(to, text)


async def _handle_brain_process(payload: Dict):
    from .brain import process_with_brain_async
    user_message = payload.get("message", "")
    wa_id = payload.get("wa_id", "")
    history = payload.get("history")
    return await process_with_brain_async(user_message, wa_id, history)


def register_queue_handlers():
    queue.register_handler("webhook_message", _handle_webhook_message)
    queue.register_handler("send_message", _handle_send_message)
    queue.register_handler("brain_process", _handle_brain_process)
