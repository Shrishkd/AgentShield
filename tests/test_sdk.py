import asyncio
import threading
from uuid import uuid4

import httpx
import pytest

from agentshield import Telemetry


def test_offline_export_preserves_application_error(monkeypatch):
    def offline(*args, **kwargs):
        raise httpx.ConnectError("collector offline")

    monkeypatch.setattr(httpx.Client, "post", offline)
    sdk = Telemetry("demo", "test")
    with pytest.raises(LookupError, match="application error"):
        with sdk.span("retrieve"):
            raise LookupError("application error")
    assert sdk.close() and sdk.dropped == 1


def test_bounded_buffer_and_source_redaction(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    exported = []

    def delayed(self, url, *, headers, json):
        exported.extend(json["events"])
        entered.set()
        release.wait(3)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", delayed)
    sdk = Telemetry("demo", "test", capacity=1)
    fields = dict(run_id=uuid4(), trace_id=uuid4(), span_id=uuid4(), operation="retrieve",
                  status="ok", event_type="check", attributes={"prompt": "sensitive", "retry_count": 0})
    try:
        assert sdk.emit_event(**fields) and entered.wait(2)
        assert sdk.emit_event(**fields)
        assert not sdk.emit_event(**fields)
        assert not sdk.close(timeout=0)
    finally:
        release.set()
        assert sdk.close()
    assert not sdk.emit_event(**fields)
    assert sdk.dropped == 2
    assert [row["attributes"] for row in exported] == [{"retry_count": 0}] * 2


def test_concurrent_span_context(monkeypatch):
    exported = []

    def capture(self, url, *, headers, json):
        exported.extend(json["events"])
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", capture)
    sdk = Telemetry("demo", "test")

    async def child():
        with sdk.span("child"):
            await asyncio.sleep(0)

    async def run():
        with sdk.span("root"):
            await asyncio.gather(child(), child())

    async def concurrent_runs():
        await asyncio.gather(run(), run())

    asyncio.run(concurrent_runs())
    assert sdk.close() and len(exported) == 6
    roots = [row for row in exported if row["operation"] == "root"]
    assert len({row["run_id"] for row in roots}) == len({row["trace_id"] for row in roots}) == 2
    for root in roots:
        children = [row for row in exported if row["parent_span_id"] == root["span_id"]]
        assert len(children) == 2
        assert all(row["trace_id"] == root["trace_id"] and row["run_id"] == root["run_id"]
                   for row in children)
