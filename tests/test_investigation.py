import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import investigation, investigation_graph as graph
from backend.app import create_app
from examples.week3 import cases
from examples.week4 import exercise


def register(client, app):
    res = client.post("/v1/apps", headers={"Authorization":"Bearer "+"a"*40}, json={"app_id":app})
    return {"Authorization":"Bearer "+res.raise_for_status().json()["api_key"]}


def seed(client, app, *, blocked=False):
    _, event = next(cases())
    event.update(app_id=app, attributes={"restricted_action_blocked":blocked}, status="error")
    client.post("/v1/events", json={"events":[event]}).raise_for_status()
    row, = client.get("/v1/incidents").json()
    return "/v1/incidents/"+row["incident_id"], event


def test_report_isolation_cache_and_restart(tmp_path):
    db = tmp_path/"db"
    with TestClient(create_app(db,"a"*40)) as client:
        owner = register(client,"owner")
        other = register(client,"other")
        client.headers.update(owner)
        base, event = seed(client,"owner",blocked=True)
        assert client.get(base+"/report").status_code == 404
        report = client.post(base+"/investigate").raise_for_status().json()
        assert report["verdict"] == "needs_more_telemetry"
        assert [f["role"] for f in report["findings"]] == ["triage","security_policy"]
        assert client.post(base+"/investigate").json() == report
        for suffix in ("/report","/report.md"):
            assert client.get(base+suffix,headers=other).status_code == 404
        assert client.post(base+"/investigate",headers=other).status_code == 404
    with TestClient(create_app(db,"a"*40)) as client:
        client.headers.update(owner)
        assert client.get(base+"/report").json() == report
        assert event["event_id"] in client.get(base+"/report.md").text
        for _ in range(2):
            event["event_id"] = str(uuid4())
            client.post("/v1/events",json={"events":[event]}).raise_for_status()
            updated = client.post(base+"/investigate").raise_for_status().json()
            assert updated["investigation_id"] != report["investigation_id"]
        assert client.get(base+"/report",params={"investigation_id":report["investigation_id"]}).json() == report
        event["event_id"] = str(uuid4())
        client.post("/v1/events",json={"events":[event]}).raise_for_status()
        assert client.post(base+"/investigate").status_code == 429


def test_interrupted_graph_resumes_after_restart(tmp_path, monkeypatch):
    db = tmp_path/"db"
    original = graph.triage
    calls = []
    def counted(state):
        calls.append(1)
        return original(state)
    monkeypatch.setattr(graph,"triage",counted)
    def interrupt(state):
        raise RuntimeError("test interruption")
    with TestClient(create_app(db,"a"*40)) as client, monkeypatch.context() as patch:
        headers = register(client,"demo")
        client.headers.update(headers)
        base, _ = seed(client,"demo")
        patch.setattr(graph,"needs_policy",interrupt)
        assert client.post(base+"/investigate").status_code == 503
    with TestClient(create_app(db,"a"*40)) as client:
        assert client.post(base+"/investigate",headers=headers).raise_for_status().json()["findings"]
    assert calls == [1]


@pytest.mark.parametrize("answer,expected_rank,attempts", [
    ({"order":[1,0]}, "ollama",1),
    ({"order":[7,0],"claim":"unsupported"}, "evidence_order",2),
    ({"order":[0,0]}, "evidence_order",2),
])
def test_model_can_only_rank_supported_findings(tmp_path, monkeypatch, answer, expected_rank, attempts):
    monkeypatch.setenv("AGENTSHIELD_RANK_MODEL","llama3.2:1b")
    calls = []
    real_client = httpx.AsyncClient
    def respond(request):
        calls.append(request)
        return httpx.Response(200,json={"response":json.dumps(answer)})
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw: real_client(transport=httpx.MockTransport(respond),**kw))
    with TestClient(create_app(tmp_path/"db","a"*40)) as client:
        client.headers.update(register(client,"demo"))
        reports = exercise(client,"demo")
    report, = [r for r in reports if len([f for f in r["findings"] if f["role"]=="root_cause"])==2]
    assert len(calls) == report["model_attempts"] == attempts
    assert report["ranking"] == expected_rank
    assert "unsupported" not in json.dumps(report)
    assert all("tools" not in json.loads(req.content) for req in calls)


def test_deadline_returns_grounded_partial(tmp_path, monkeypatch):
    from langgraph.pregel import Pregel
    async def delayed(self, config):
        await asyncio.sleep(2)
    monkeypatch.setattr(Pregel,"aget_state",delayed)
    monkeypatch.setattr(graph,"DEADLINE",0.05)
    with TestClient(create_app(tmp_path/"db","a"*40)) as client:
        client.headers.update(register(client,"demo"))
        base, event = seed(client,"demo")
        report = client.post(base+"/investigate").raise_for_status().json()
        assert "deadline" in report["limitations"][0]
        assert report["findings"][0]["evidence"][0]["event_id"] == event["event_id"]


def test_other_trace_cannot_support_causal_claim(tmp_path):
    with TestClient(create_app(tmp_path/"db","a"*40)) as client:
        client.headers.update(register(client,"demo"))
        base, parent = seed(client,"demo")
        child = dict(parent, event_id=str(uuid4()), trace_id=str(uuid4()), span_id=str(uuid4()),
                     parent_span_id=parent["span_id"], timestamp="2020-01-01T00:00:00Z",
                     attributes={"timed_out":True}, event_type="span_finished")
        client.post("/v1/events",json={"events":[child]}).raise_for_status()
        report = client.post(base+"/investigate").raise_for_status().json()
        assert report["verdict"] == "needs_more_telemetry"
        assert child["event_id"] not in json.dumps(report)


def test_concurrent_and_repeated_failure_budget(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    async def unavailable(*args):
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        raise RuntimeError("private diagnostic")
    monkeypatch.setattr(graph,"investigate",unavailable)
    with TestClient(create_app(tmp_path/"db","a"*40)) as client, ThreadPoolExecutor(1) as worker:
        client.headers.update(register(client,"demo"))
        base, _ = seed(client,"demo")
        first = worker.submit(client.post,base+"/investigate")
        try:
            assert entered.wait(3)
            assert client.post(base+"/investigate").status_code == 409
        finally:
            release.set()
        assert first.result().status_code == 503
        partial = client.post(base+"/investigate").raise_for_status().json()
        assert partial["verdict"] == "needs_more_telemetry" and partial["model_attempts"] is None
        assert "private diagnostic" not in json.dumps(partial)
        assert client.post(base+"/investigate").json() == partial


def test_selected_detector_jobs_survive_restart(tmp_path, monkeypatch):
    db = tmp_path/"db"
    monkeypatch.setenv("AGENTSHIELD_AUTO_CATEGORIES","timeout")
    with monkeypatch.context() as before_restart:
        before_restart.setattr(investigation, "work_once", lambda *args: False)
        with TestClient(create_app(db,"a"*40)) as client:
            auth = register(client,"demo")
            client.headers.update(auth)
            events = [dict(e, app_id="demo") for category,e in cases() if category in {"timeout","invalid_schema"}]
            client.post("/v1/events",json={"events":events}).raise_for_status()
            rows = client.get("/v1/incidents").json()
            paths = {r["category"]:"/v1/incidents/"+r["incident_id"]+"/report" for r in rows}
            assert client.get(paths["timeout"]).status_code == 404
    with TestClient(create_app(db,"a"*40)) as client:
        client.headers.update(auth)
        deadline = time.monotonic()+8
        while True:
            res = client.get(paths["timeout"])
            if res.status_code == 200:
                break
            assert res.status_code == 404 and time.monotonic() < deadline
            time.sleep(0.05)
        assert res.json()["category"] == "timeout"
        assert client.get(paths["invalid_schema"]).status_code == 404
