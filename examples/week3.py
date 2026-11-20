"""Run deterministic fixtures against a disposable loopback collector."""
import argparse
import json
import os
import secrets
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import httpx

from agentshield import Event
from examples.week1 import local_service

APP = "week-three-demo"
POLICY = {"latency_ms": {"retrieve": 200}, "max_retries": 2,
          "require_citations_on": ["citation_ids"]}


def cases():
    seeds = [
        ("invalid_schema", "parse", {"schema_valid": False}),
        ("timeout", "generate", {"timed_out": True}),
        ("tool_exception", "retrieve", {"tool_exception": True}),
        ("verification_failed", "verify", {"claim_verified": False}),
        ("missing_citation", "citation_ids", {"has_citations": False}),
        ("invalid_citation", "citation_ids", {"citation_ids_valid": False}),
        ("slow_span", "retrieve", {}),
        ("retry_limit", "generate", {"retry_count": 3}),
        ("execution_error", "request", {}),
        (None, "retrieve", {"schema_valid": True, "retry_count": 2}),
        (None, "citation_ids", {"has_citations": True, "citation_ids_valid": True}),
        (None, "optional_citation", {"has_citations": False}),
    ]
    for category, operation, attrs in seeds:
        event = Event(schema_version="1.0", app_id=APP, run_id=uuid4(), trace_id=uuid4(), span_id=uuid4(),
                      operation=operation, event_type="span_finished",
                      duration_ms=201 if category == "slow_span" else 200,
                      status="error" if category in {"execution_error","timeout","tool_exception"} else "ok",
                      attributes=attrs)
        yield category, event.model_dump(mode="json")


def exercise(client):
    seeded = list(cases())
    batch = {"events": [event for _, event in seeded]}
    expected = {(category, event["run_id"]): event for category, event in seeded if category}
    assert client.post("/v1/events", json=batch).raise_for_status().json()["inserted"] == len(seeded)
    assert client.post("/v1/events", json=batch).raise_for_status().json()["duplicates"] == len(seeded)
    incidents = client.get("/v1/incidents").raise_for_status().json()
    assert len(incidents) == len(expected)
    for row in incidents:
        event = expected.pop((row["category"], row["run_id"]))
        detail = client.get("/v1/incidents/"+row["incident_id"]).raise_for_status().json()
        evidence, = detail["evidence"]
        assert evidence["reason"] and evidence["event"] == event
    assert not expected
    return len(incidents), len(seeded) - len(incidents)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explore", action="store_true", help="keep disposable dashboard open until Enter")
    args = parser.parse_args()
    with TemporaryDirectory() as folder:
        policy = Path(folder) / "policy.json"
        policy.write_text(json.dumps({APP: POLICY}), encoding="utf-8")
        admin = secrets.token_urlsafe(32)
        with patch.dict(os.environ, AGENTSHIELD_POLICIES=str(policy)), local_service(Path(folder)/"db", admin) as url:
            with httpx.Client(base_url=url, trust_env=False) as client:
                registration = client.post("/v1/apps", headers={"Authorization": "Bearer "+admin}, json={"app_id":APP})
                key = registration.raise_for_status().json()["api_key"]
                client.headers["Authorization"] = "Bearer "+key
                failed, clean = exercise(client)
                print(f"{failed} seeded incidents, {clean} clean controls, evidence and duplicate replay verified.")
                if args.explore:
                    client.get("/explorer/").raise_for_status()
                    print(f"Explorer {url}/explorer/\nTemporary demo app key {key}")
                    input("Press Enter to stop the disposable collector. ")


if __name__ == "__main__":
    main()
