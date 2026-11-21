from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from examples.week3 import cases
from examples.week7 import ROOT, run_app
from integrations.mailmate.execution import project_execution, upload


def auth(key):
    return {"Authorization": "Bearer " + key}


def test_fleet_read_scope_and_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTSHIELD_VIEWER_KEY", "v" * 40)
    viewer, admin = auth("v" * 40), auth("a" * 40)
    with TestClient(create_app(tmp_path / "db", "a" * 40)) as client:
        _, event = next(cases())
        for app in ("one", "two"):
            key = client.post("/v1/apps", headers=admin, json={"app_id": app}).json()["api_key"]
            owner = auth(key)
            batch = {"events": [{**event, "app_id": app}]}
            assert client.post("/v1/events", headers=owner, json=batch).is_success
        path = "/v1/fleet/incidents"
        assert client.get("/v1/fleet/apps", headers=viewer).json() == ["one", "two"]
        assert {i["app_id"] for i in client.get(path, headers=viewer).json()} == {"one", "two"}
        row, = client.get(path + "?app_id=one", headers=viewer).json()
        detail = path + "/" + row["incident_id"]
        assert client.get(detail + "?app_id=one", headers=viewer).json()["evidence"][0]["event"]["app_id"] == "one"
        assert client.get(detail + "?app_id=two", headers=viewer).status_code == 404
        for headers in ({}, owner, admin):
            assert client.get(path, headers=headers).status_code == 401
        assert client.get("/v1/incidents", headers=owner).json()[0]["app_id"] == "two"
        scoped = "/v1/incidents/" + row["incident_id"]
        assert client.post(scoped + "/investigate", headers=viewer).status_code == 401
        assert client.patch(scoped, headers=viewer, json={"status": "resolved"}).status_code == 401
        assert client.post("/v1/events", headers=viewer, json=batch).status_code == 401
        assert client.post("/v1/apps", headers=viewer, json={"app_id": "three"}).status_code == 401
    monkeypatch.delenv("AGENTSHIELD_VIEWER_KEY")
    with TestClient(create_app(tmp_path / "db", "a" * 40)) as client:
        assert client.get(path, headers=viewer).status_code == 401
    monkeypatch.setenv("AGENTSHIELD_VIEWER_KEY", key)
    with pytest.raises(ValueError, match="application credential"), TestClient(create_app(tmp_path / "db", "a" * 40)):
        pass


def test_n8n_projection_is_private_and_idempotent():
    node = {"startTime": 10000, "executionTime": 21, "executionStatus": "success",
            "data": {"message": "sensitive"}}
    runs = {"Webhook": [node], "Create draft": [{**node, "error": {"message": "sensitive"}}],
            "sensitive": [node], "Build draft": [{**node, "executionStatus": "waiting"}]}
    execution = {"id": "sensitive", "data": {"resultData": {"runData": runs}}}
    events = list(project_execution("mailmate", execution, "instance"))
    assert events == list(project_execution("mailmate", deepcopy(execution), "instance"))
    success, failure = events
    assert failure.attributes == {"tool_exception": True} and success.status == "ok"
    assert failure.trace_id == success.trace_id and failure.span_id != success.span_id
    assert failure.timestamp.timestamp() == 10.021
    assert all("sensitive" not in e.model_dump_json() for e in events)
    other = list(project_execution("mailmate", execution, "second"))
    assert other[-1].run_id != failure.run_id


def test_large_execution_reimport(tmp_path):
    node = {"startTime": 10000, "executionTime": 1, "executionStatus": "error"}
    execution = {"id": "1", "data": {"resultData": {"runData": {"Create draft": [node] * 215}}}}
    with TestClient(create_app(tmp_path / "db", "a" * 40)) as client:
        key = client.post("/v1/apps", headers=auth("a" * 40), json={"app_id": "mailmate"}).json()["api_key"]
        client.headers.update(auth(key))
        for _ in range(2):
            assert upload(client, project_execution("mailmate", execution, "instance")) == 215
        incident, = client.get("/v1/incidents").json()
        assert incident["evidence_count"] == 215


def test_mailmate_with_collector_offline(tmp_path, monkeypatch):
    import os
    import socket

    if not Path(os.getenv("MAILMATE_ROOT", ROOT.parent / "MailMate")).is_dir():
        pytest.skip("Set MAILMATE_ROOT to exercise the separate MailMate checkout")
    # The child explicitly expects delivery loss in this mode; MailMate's results must still match.
    monkeypatch.setenv("WEEK7_EXPECT_DROPS", "1")
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        run_app("mailmate", f"http://127.0.0.1:{unavailable.getsockname()[1]}", "fixture", tmp_path)
