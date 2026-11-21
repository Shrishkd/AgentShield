"""Exercise the MONDAY integration with a real collector and scripted Ollama transport."""
import argparse
import json
import os
import secrets
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from examples.week1 import local_service

ROOT = Path(__file__).resolve().parents[1]


def exercise(client, url, key, monday_root):
    python = Path(os.getenv("MONDAY_PYTHON") or monday_root/".venv"/(
        "Scripts/python.exe" if os.name == "nt" else "bin/python"))
    if not python.is_file():
        raise RuntimeError("Set MONDAY_ROOT to the MONDAY checkout or MONDAY_PYTHON to its interpreter")
    env = {**os.environ, "PYTHONPATH":os.pathsep.join(map(str,(ROOT,ROOT/"sdk",monday_root))),
           "AGENTSHIELD_URL":url, "AGENTSHIELD_APP_KEY":key}
    with TemporaryDirectory() as cwd:
        child = subprocess.run([str(python.resolve()),"-B","-m","integrations.monday.fixture"],
                               cwd=cwd, env=env, stdout=subprocess.PIPE, text=True, check=True, timeout=90)
    runs = json.loads(child.stdout)
    incidents = client.get("/v1/incidents").raise_for_status().json()
    expected = {"clean":set(), "corrected":{"verification_failed"}, "fallback":{"verification_failed"},
                "escaped":{"escaped_claim"}, "recovered":{"invalid_schema","execution_error"},
                "model_switch":{"execution_error"}, "exhausted":{"invalid_schema","execution_error"}}
    for case, run in runs.items():
        events = client.get("/v1/runs/"+run).raise_for_status().json()["events"]
        rows = [i for i in incidents if i["run_id"] == run]
        assert {i["category"] for i in rows} == expected[case], case
        agents = [e for e in events if e["operation"]=="resume_tailor" and e["event_type"]=="span_finished"]
        agent, = agents
        assert any(e["operation"]=="job_matcher" for e in events)
        assert len({e["trace_id"] for e in events}) == 1
        assert not any(s in json.dumps(events) for s in ("fixture@example.invalid","Fixture Company",
                        "fixture-fact","Built Python","fixture-model","bad JSON","Kubernetes","Fortran"))
        rounds = [e for e in events if e["operation"]=="structured_generation" and e["parent_span_id"]==agent["span_id"]]
        if case in {"fallback","corrected"}:
            # Windows timestamps can tie. MONDAY records the generation index explicitly.
            rounds.sort(key=lambda e: e["attributes"]["generation_index"])
            assert [e["attributes"]["generation_index"] for e in rounds] == [1,2]
            rejected = [e for e in events if e["attributes"].get("claim_verified") is False]
            assert rejected and all(e["operation"]=="check_bullet" and e["parent_span_id"]==agent["span_id"]
                                    for e in rejected)
            assembled, = [e for e in events if e["operation"]=="assembled_draft"]
            assert assembled["attributes"]["bullet_fallback_used"] == (case=="fallback")
            report = client.post("/v1/incidents/"+rows[0]["incident_id"]+"/investigate").raise_for_status().json()
            first_rejections = {e["event_id"] for e in rejected if e["attributes"]["generation_index"] == 1}
            citations = [{ref["event_id"] for ref in f["evidence"]} for f in report["findings"]
                         if "check_bullet" in f["statement"]]
            assert any({agent["event_id"], rounds[0]["event_id"]} <= ids and ids & first_rejections for ids in citations)
        if case == "escaped":
            assert not any(e["attributes"].get("claim_verified") is False for e in events)
            detail = client.get("/v1/incidents/"+rows[0]["incident_id"]).raise_for_status().json()
            assert detail["evidence"][0]["event"]["operation"] == "fixture_output_audit"
            report = client.post("/v1/incidents/"+rows[0]["incident_id"]+"/investigate").raise_for_status().json()
            assert any("returned draft" in f["statement"] and "delivery remains unverified" in f["statement"]
                       for f in report["findings"] if f["role"]=="security_policy")
        if case == "recovered":
            assert rounds[0]["attributes"]["retry_count"] == 2 and rounds[0]["status"] == "ok"
        if case == "model_switch":
            assert rounds[0]["attributes"]["model_slot"] == 2 and rounds[0]["status"] == "ok"
        if case == "exhausted":
            assert rounds[0]["attributes"]["models_exhausted"] and rounds[0]["status"] == "error"
        print(case,run,", ".join(sorted(expected[case])) or "clean")
    assert set(runs) == set(expected)
    return runs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monday-root", type=Path, default=Path(os.getenv("MONDAY_ROOT",ROOT.parent/"Monday")))
    parser.add_argument("--explore", action="store_true")
    args = parser.parse_args()
    admin = secrets.token_urlsafe(32)
    with TemporaryDirectory() as tmp, patch.dict(os.environ, AGENTSHIELD_POLICIES="", AGENTSHIELD_AUTO_CATEGORIES="",
                                                 AGENTSHIELD_RANK_MODEL=""), local_service(Path(tmp)/"db",admin) as url:
        with httpx.Client(base_url=url, trust_env=False,timeout=45) as client:
            response = client.post("/v1/apps",json={"app_id":"monday-demo"},headers={"Authorization":"Bearer "+admin})
            key = response.raise_for_status().json()["api_key"]
            client.headers["Authorization"] = "Bearer "+key
            exercise(client,url,key,args.monday_root.resolve())
            if args.explore:
                client.get("/explorer/").raise_for_status()
                print(f"Explorer {url}/explorer/\nTemporary application key {key}")
                input("Press Enter to stop the collector. ")


if __name__ == "__main__":
    main()
