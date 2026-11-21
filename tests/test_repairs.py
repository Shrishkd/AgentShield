import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend import verifier
from backend.app import create_app
from examples.week6 import proposals

ADMIN = {"Authorization": "Bearer "+"a"*40}


def register(client, app):
    return client.post("/v1/apps", headers=ADMIN, json={"app_id": app}).raise_for_status().json()["api_key"]


@pytest.mark.parametrize("tag", [601, 602])
def test_reproducible_comparisons_and_no_op_candidate(tag):
    fixture, _, digest = verifier.bundle(tag)
    change = verifier.Change(setting=fixture.setting, before=False, after=True)
    first = verifier.verify(tag, digest, change)
    assert first.verdict == "passed"
    assert first == verifier.verify(tag, digest, change)
    assert verifier.verify(tag, digest, change.model_copy(update={"after":False})).verdict == "failed"
    regression = [c.model_copy(update={"candidate":"abstain"}) if c.kind=="control" else c for c in first.cases]
    assert verifier.compare(regression).verdict == "failed"
    assert verifier.verify(tag,"0"*64,change).verdict == "unverified"


def test_worker_capabilities_and_deadline(monkeypatch, tmp_path):
    shutil.copytree(verifier.DIRECTORY, tmp_path/"bundle")
    monkeypatch.setattr(verifier,"DIRECTORY",tmp_path/"bundle")
    wat = verifier.DIRECTORY/"policies.wat"
    wat.write_text(wat.read_text().replace("(module", '(module (import "host" "clock" (func))',1))
    fixture, _, digest = verifier.bundle(601)
    change = verifier.Change(setting=fixture.setting,before=False,after=True)
    assert verifier.verify(601,digest,change).verdict == "unverified"
    def timeout(*args, **kwargs):
        assert "AGENTSHIELD_ADMIN_KEY" not in kwargs["env"] and kwargs["timeout"] == 5
        raise subprocess.TimeoutExpired("worker",5)
    monkeypatch.setenv("AGENTSHIELD_ADMIN_KEY","sensitive-test-value")
    monkeypatch.setattr(subprocess,"run",timeout)
    assert "deadline" in verifier.verify(601,digest,change).reason


def test_review_gate_and_persistence(tmp_path):
    db = tmp_path/"db"
    with TestClient(create_app(db,"a"*40)) as client:
        owner = register(client,"week-six-demo")
        other = register(client,"other")
        client.headers["Authorization"] = "Bearer "+owner
        bound, unbound = {}, None
        for p in proposals(client):
            if p["fixture_id"]:
                bound[p["fixture_id"]] = p
            else:
                unbound = p
        assert len(bound) == 2 and unbound
        p = bound[601]
        base = "/v1/proposals/"+p["proposal_id"]
        incident = "/v1/incidents/"+p["incident_id"]
        params = {"investigation_id": p["investigation_id"]}
        assert client.post(incident+"/proposals", params=params).json()["proposal"] == p
        assert client.get(incident+"/proposal", params=params).json()["proposal"] == p
        stranger = {"Authorization":"Bearer "+other}
        assert client.get(base, headers=stranger).status_code == 404
        assert client.post(incident+"/proposals", params=params, headers=stranger).status_code == 404
        assert client.post(base+"/decision", headers=ADMIN, json={"choice":"accept"}).status_code == 409
        auth = {"request_id":str(uuid4()), "bundle_digest":p["bundle_digest"]}
        assert client.post(base+"/verify",json=auth).status_code == 401
        assert client.post(base+"/verify",headers=ADMIN,json={**auth,"bundle_digest":"wrong"}).status_code == 409
        assert client.post(base+"/verify",headers=ADMIN,json={**auth,"command":"echo"}).status_code == 422
        replay = client.post(base+"/verify",headers=ADMIN,json=auth).raise_for_status().json()
        assert replay["result"]["verdict"] == "passed"
        assert client.post(base+"/verify",headers=ADMIN,json=auth).json() == replay
        lookup = base+"/verifications/"+replay["verification_id"]
        assert client.get(lookup).json() == replay
        assert client.get(lookup, headers=stranger).status_code == 404
        assert client.get(base).json()["decision"] is None
        decision = {"choice":"accept","verification_id":replay["verification_id"]}
        assert client.post(base+"/decision",json=decision).status_code == 401
        assert client.post(base+"/decision",headers=ADMIN,json={**decision,"verification_id":str(uuid4())}).status_code == 409
        accepted = client.post(base+"/decision",headers=ADMIN,json=decision).raise_for_status().json()
        assert client.post(base+"/decision",headers=ADMIN,json=decision).json() == accepted
        assert client.post(base+"/decision",headers=ADMIN,json={"choice":"reject"}).status_code == 409
        without = "/v1/proposals/"+unbound["proposal_id"]
        attempt = client.post(without+"/verify",headers=ADMIN,json={"request_id":str(uuid4()),"bundle_digest":None}).json()
        assert attempt["result"]["verdict"] == "unverified"
        assert client.post(without+"/decision",headers=ADMIN,json={"choice":"accept","verification_id":attempt["verification_id"]}).status_code == 409
        assert client.post(without+"/decision",headers=ADMIN,json={"choice":"reject"}).json()["choice"] == "reject"
    with TestClient(create_app(db,"a"*40)) as client:
        review = client.get(base,headers={"Authorization":"Bearer "+owner}).raise_for_status().json()
        assert review["decision"] == accepted and review["replay"] == replay


def test_rejection_wins_over_inflight_verification(tmp_path, monkeypatch):
    started, finish = threading.Event(), threading.Event()
    def slow_verifier(*args):
        started.set()
        assert finish.wait(5)
        return verifier.Result(verdict="passed", reason="fixture")
    with TestClient(create_app(tmp_path/"db","a"*40)) as client, ThreadPoolExecutor(1) as pool:
        client.headers["Authorization"] = "Bearer "+register(client,"week-six-demo")
        p = next(p for p in proposals(client) if p["fixture_id"])
        base = "/v1/proposals/"+p["proposal_id"]
        auth = {"request_id":str(uuid4()), "bundle_digest":p["bundle_digest"]}
        monkeypatch.setattr(verifier,"verify",slow_verifier)
        future = pool.submit(client.post, base+"/verify", headers=ADMIN, json=auth)
        try:
            assert started.wait(3)
            client.post(base+"/decision", headers=ADMIN, json={"choice":"reject"}).raise_for_status()
        finally:
            finish.set()
        assert future.result().status_code == 409
        assert client.get(base).json()["replay"] is None


def test_regression_verdict_blocks_acceptance(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path/"db","a"*40)) as client:
        client.headers["Authorization"] = "Bearer "+register(client,"week-six-demo")
        p = next(p for p in proposals(client) if p["fixture_id"])
        verify = verifier.verify
        def unchanged_candidate(tag, digest, change):
            return verify(tag, digest, change.model_copy(update={"after":change.before}))
        monkeypatch.setattr(verifier,"verify",unchanged_candidate)
        base = "/v1/proposals/"+p["proposal_id"]
        req = {"request_id":str(uuid4()), "bundle_digest":p["bundle_digest"]}
        result = client.post(base+"/verify",headers=ADMIN,json=req).raise_for_status().json()
        assert result["result"]["verdict"] == "failed"
        response = client.post(base+"/decision",headers=ADMIN,json={"choice":"accept","verification_id":req["request_id"]})
        assert response.status_code == 409
        assert client.get(base).json()["decision"] is None
