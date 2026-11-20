from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event as Signal, Lock, Thread
from time import monotonic
from uuid import UUID, uuid4

import httpx

from .contracts import Event, Status


@dataclass
class SpanOutcome:
    status: Status = "ok"


class Telemetry:
    """Best-effort exporter. Create one per application process."""

    def __init__(self, app_id: str, key: str, url: str = "http://127.0.0.1:8765",
                 capacity: int = 256):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.app_id = app_id
        self._url = url.rstrip("/") + "/v1/events"
        self._key = key
        self._queue: Queue[dict] = Queue(maxsize=capacity)
        self._closed = Signal()
        self._lock = Lock()
        self._context: ContextVar[tuple[UUID, UUID, UUID] | None] = ContextVar(
            "agentshield_span", default=None
        )
        self.dropped = 0
        self._worker = Thread(target=self._export, daemon=True)
        self._worker.start()

    def emit_event(self, **fields) -> bool:
        """Return whether a sanitized event entered the queue."""
        try:
            event = Event(schema_version="1.0", app_id=self.app_id, **fields)
            payload = event.model_dump(mode="json")
            with self._lock:
                if self._closed.is_set():
                    self.dropped += 1
                    return False
                self._queue.put_nowait(payload)
            return True
        except (TypeError, ValueError, Full):
            with self._lock:
                self.dropped += 1
            return False

    @contextmanager
    def span(self, operation: str, *, record_start=False, attributes=None, outcome: SpanOutcome | None = None):
        """Time a sync or async block. Each outermost span starts a run."""
        parent = self._context.get()
        run, trace, parent_span = parent or (uuid4(), uuid4(), None)
        span_id = uuid4()
        token = self._context.set((run, trace, span_id))
        start = monotonic()
        result = outcome or SpanOutcome()
        fields = dict(operation=operation, run_id=run, trace_id=trace, span_id=span_id,
                      parent_span_id=parent_span)
        try:
            if record_start:
                self.emit_event(**fields, event_type="span_started", status="ok")
            yield span_id
        except BaseException:
            result.status = "error"
            raise
        finally:
            self._context.reset(token)
            self.emit_event(**fields, event_type="span_finished", status=result.status,
                            attributes=attributes or {}, duration_ms=(monotonic() - start) * 1000)

    @property
    def current_run_id(self) -> UUID | None:
        context = self._context.get()
        return context[0] if context else None

    def record_check(self, operation, status, attributes):
        context = self._context.get()
        if context is None:
            return False
        run, trace, span_id = context
        return self.emit_event(operation=operation, run_id=run, trace_id=trace, span_id=uuid4(),
                               parent_span_id=span_id, event_type="validation", status=status,
                               attributes=attributes)

    def _export(self):
        with httpx.Client(timeout=1.0, trust_env=False) as client:
            while not self._closed.is_set() or not self._queue.empty():
                try:
                    batch = [self._queue.get(timeout=0.05)]
                except Empty:
                    continue
                while len(batch) < 100:
                    try:
                        batch.append(self._queue.get_nowait())
                    except Empty:
                        break
                try:
                    response = client.post(self._url, headers={"Authorization": f"Bearer {self._key}"},
                                           json={"events": batch})
                    response.raise_for_status()
                except (httpx.HTTPError, httpx.InvalidURL, ValueError):
                    with self._lock:
                        self.dropped += len(batch)

    def close(self, timeout: float = 2.0) -> bool:
        """Bounded drain at shutdown. True means the exporter exited."""
        with self._lock:
            self._closed.set()
        self._worker.join(timeout)
        return not self._worker.is_alive()
