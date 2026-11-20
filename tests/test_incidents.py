import json
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app
from examples.week3 import APP, POLICY, cases, exercise

ADMIN = {"Authorization": "Bearer " + "a"*40}


def register(client, app):
    key = client.post("/v1/apps", headers=ADMIN, json={"app_id": app}).json()["api_key"]
    return {"Authorization": "Bearer "+key}


def test_seeded_cases_and_per_app_policy(tmp_path):
    path = tmp_path/"rules"
    path.write_text(json.dumps({APP: POLICY}))
    with TestClient(create_app(tmp_path/"db", "a"*40, path)) as client:
        client.headers.update(register(client, APP))
        assert exercise(client) == (9, 3)
        client.headers.update(register(client, "other"))
        payload = [dict(event, app_id="other") for category, event in cases()
                   if category in {"missing_citation", "slow_span", "retry_limit"}]
        assert client.post("/v1/events", json={"events":payload}).is_success
        assert client.get("/v1/incidents").json() == []


def test_lifecycle_dedup_atomicity_and_isolation(tmp_path):
    with TestClient(create_app(tmp_path/"db", "a"*40)) as client:
        owner, other = register(client, APP), register(client, "other")
        _, event = next(cases())
        send = lambda events: client.post("/v1/events", headers=owner, json={"events":events})
        assert send([event]).is_success
        row, = client.get("/v1/incidents?severity=high&category=invalid_schema", headers=owner).json()
        path = "/v1/incidents/"+row["incident_id"]
        assert client.get(path, headers=other).status_code == 404
        assert client.get(path).status_code == 401
        assert client.patch(path, headers=owner, json={"status":"resolved"}).status_code == 401
        for state in ("acknowledged","resolved"):
            assert client.patch(path, headers=ADMIN, json={"status":state}).json()["status"] == state
        earlier = {**event, "event_id":str(uuid4()), "timestamp":"2020-01-01T00:00:00Z"}
        assert send([earlier, {**event, "operation":"conflict"}]).status_code == 409
        assert client.get(path, headers=owner).json()["incident"]["evidence_count"] == 1
        assert send([earlier, event]).is_success
        result = client.get(path+"?limit=1", headers=owner).json()
        assert result["incident"]["first_seen"].startswith("2020-")
        assert result["incident"]["evidence_count"] == 2
        assert result["incident"]["status"] == "resolved"
        assert result["evidence"][0]["event"]["event_id"] == earlier["event_id"]
        assert client.get("/v1/incidents?status=open", headers=owner).json() == []


def test_existing_events_backfilled_once(tmp_path):
    path = tmp_path/"db"
    app = create_app(path, "a"*40)
    with TestClient(app) as client:
        auth = register(client, APP)
    _, event = next(cases())
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM detector_migrations")
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (APP, event["event_id"], event["run_id"],
                   event["trace_id"], event["status"], event["timestamp"], json.dumps(event)))
    for _ in range(2):
        with TestClient(app) as client:
            row, = client.get("/v1/incidents", headers=auth).json()
            assert row["category"] == "invalid_schema" and row["evidence_count"] == 1
