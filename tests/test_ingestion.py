import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentshield import Event
from backend.app import MAX_BYTES, create_app

ADMIN = "test-only-" * 5


@pytest.fixture
def service(tmp_path):
    with TestClient(create_app(tmp_path / "db", ADMIN)) as client:
        yield client


def register(client, app_id="demo"):
    response = client.post("/v1/apps", headers={"Authorization": f"Bearer {ADMIN}"},
                           json={"app_id": app_id})
    assert response.status_code == 201
    return {"Authorization": "Bearer " + response.json()["api_key"]}


@pytest.fixture
def record():
    return Event(schema_version="1.0", app_id="demo", run_id=uuid4(), trace_id=uuid4(),
                 span_id=uuid4(), operation="retrieve", event_type="check",
                 status="ok").model_dump(mode="json")


def test_permissions(service, record, tmp_path):
    assert service.post("/v1/apps", json={"app_id": "demo"}).status_code == 401
    a, b = register(service), register(service, "second")
    assert service.post("/v1/apps", headers=a, json={"app_id": "other"}).status_code == 401
    assert service.post("/v1/events", headers=b, json={"events": [record]}).status_code == 403
    assert service.post("/v1/events", headers=a, json={"events": [record]}).is_success
    assert service.get("/v1/events", headers=b).json() == []
    assert service.get("/v1/events").status_code == 401
    with sqlite3.connect(tmp_path / "db") as db:
        key_hash, = db.execute("SELECT key_hash FROM applications WHERE id='demo'").fetchone()
        assert len(key_hash) == 32 and key_hash != a["Authorization"][7:].encode()


def test_run_queries_scope_pagination_and_error_filter(service, record):
    owner, second = register(service), register(service, "another")
    run_id = record["run_id"]
    error = {**record, "event_id": str(uuid4()), "status": "error", "timestamp": "2026-12-01T10:00:00Z"}
    clean = {**record, "event_id": str(uuid4()), "run_id": str(uuid4())}
    assert service.post("/v1/events", headers=owner, json={"events": [record, error, clean]}).is_success
    path = "/v1/runs/" + run_id
    runs = service.get("/v1/runs?status=error", headers=owner).json()
    assert [(r["run_id"], r["event_count"], r["status"]) for r in runs] == [(run_id, 2, "error")]
    assert service.get("/v1/runs?status=ok", headers=owner).json()[0]["run_id"] == clean["run_id"]
    first = service.get(path + "?limit=1", headers=owner).json()
    next_page = service.get(path + "?limit=1&offset=1", headers=owner).json()
    assert first["run"] == next_page["run"] == runs[0]
    assert {first["events"][0]["event_id"], next_page["events"][0]["event_id"]} == {record["event_id"], error["event_id"]}
    assert service.get("/v1/runs", headers=second).json() == []
    assert service.get(path, headers=second).status_code == 404
    assert service.get(path).status_code == 401


def test_immutable_atomic_batch(service, record, tmp_path):
    headers = register(service)
    post = lambda events: service.post("/v1/events", headers=headers, json={"events": events})
    assert post([record, record]).json() == {"inserted": 1, "duplicates": 1}
    other = {**record, "event_id": str(uuid4())}
    assert post([other, {**record, "status": "error"}]).status_code == 409
    with TestClient(create_app(tmp_path / "db", ADMIN)) as restarted:
        assert restarted.get("/v1/events", headers=headers).json() == [record]


def test_request_limits_and_json(service, record):
    headers = register(service)
    assert service.post("/v1/events", content=b"{", headers=headers).status_code == 422
    chunks = iter([b"a" * MAX_BYTES, b"b"])
    assert service.post("/v1/events", content=chunks, headers=headers).status_code == 413
    assert service.post("/v1/events", json={"events": [record] * 101}, headers=headers).status_code == 422
    assert service.get("/v1/events", headers=headers).json() == []


@pytest.mark.parametrize("change", [{"schema_version": "9"}, {"timestamp": "2026-01-01T10:00:00"},
                                    {"operation": "private@example.com"}, {"extra": "private@example.com"}])
def test_invalid_contract(service, record, change):
    response = service.post("/v1/events", headers=register(service), json={"events": [{**record, **change}]})
    assert response.status_code == 422 and "private@example.com" not in response.text


def test_server_redaction(service, record, tmp_path):
    headers = register(service)
    record["attributes"] = {"resume": {"email": "private@example.com"}, "claim_verified": False,
                            "retry_count": "private@example.com", "retrieved_count": 2}
    assert service.post("/v1/events", headers=headers, json={"events": [record]}).is_success
    row, = service.get("/v1/events", headers=headers).json()
    assert row["attributes"] == {"claim_verified": False, "retrieved_count": 2}
    with sqlite3.connect(tmp_path / "db") as db:
        assert "private@example.com" not in db.execute("SELECT payload FROM events").fetchone()[0]
