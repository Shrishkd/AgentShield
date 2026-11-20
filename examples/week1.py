"""Exercise the installed SDK against a real loopback server and disposable database."""
import secrets
import socket
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
from uuid import uuid4

import httpx
import uvicorn

from agentshield import Telemetry
from backend.app import create_app


@contextmanager
def local_service(path, admin):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(path, admin), log_level="error",
                                               access_log=False))
        worker = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        worker.start()
        try:
            deadline = monotonic() + 10
            while not server.started:
                if not worker.is_alive() or monotonic() > deadline:
                    raise RuntimeError("local service failed to start")
                sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            worker.join(10)


def exercise(url, admin):
    app_id = "week-one-demo"
    run, trace, root = uuid4(), uuid4(), uuid4()
    with httpx.Client(base_url=url, trust_env=False) as client:
        registration = client.post("/v1/apps", json={"app_id": app_id},
                                   headers={"Authorization": f"Bearer {admin}"})
        key = registration.raise_for_status().json()["api_key"]
        client.headers["Authorization"] = f"Bearer {key}"
        sdk = Telemetry(app_id, key, url)
        try:
            for index in range(100):
                accepted = sdk.emit_event(
                    run_id=run, trace_id=trace, span_id=root if index == 0 else uuid4(),
                    parent_span_id=None if index == 0 else root, operation="fixture_check",
                    event_type="check", status="error" if index % 10 == 1 else "ok",
                    attributes={"retrieved_count": index, "prompt": "private fixture"})
                assert accepted
        finally:
            drained = sdk.close(timeout=10)
        assert drained and sdk.dropped == 0
        params = {"run_id": str(run), "trace_id": str(trace)}
        rows = client.get("/v1/events", params=params).raise_for_status().json()
        assert len({row["event_id"] for row in rows}) == 100
        assert all(set(row["attributes"]) == {"retrieved_count"} for row in rows)
        errors = client.get("/v1/events", params={**params, "status": "error"}).raise_for_status().json()
        assert len(errors) == 10 and all(row["status"] == "error" for row in errors)
        replay = client.post("/v1/events", json={"events": rows}).raise_for_status().json()
        assert replay == {"inserted": 0, "duplicates": 100}
        assert client.get("/v1/events", params={"offset": 100}).raise_for_status().json() == []
        return len(rows), len(errors), replay["duplicates"]


if __name__ == "__main__":
    admin = secrets.token_urlsafe(32)
    with TemporaryDirectory() as folder, local_service(Path(folder) / "demo.sqlite3", admin) as url:
        total, errors, duplicates = exercise(url, admin)
        print(f"{total} unique events retrieved, {errors} filtered errors, {duplicates} duplicates ignored")
