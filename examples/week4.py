"""Investigate a propagated failure, a rejected claim, and an ambiguous error."""
import secrets
import argparse
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from unittest.mock import patch

import httpx

from agentshield import Event
from examples.week1 import local_service


def exercise(client, app="week-four-demo"):
    run, trace, parent = uuid4(), uuid4(), uuid4()

    def make(operation, attrs, *, root=False):
        return Event(schema_version="1.0", app_id=app, run_id=run, trace_id=trace,
                     span_id=parent if root else uuid4(), parent_span_id=None if root else parent,
                     operation=operation, status="error", event_type="span_finished", attributes=attrs)

    events = [make("embed", {"timed_out":True}), make("retrieve", {"tool_exception":True}),
              make("request", {}, root=True)]
    run, trace, parent = uuid4(), uuid4(), uuid4()
    events.append(make("claim_check", {"claim_verified":False}, root=True))
    run, trace, parent = uuid4(), uuid4(), uuid4()
    events.append(make("request", {}, root=True))
    client.post("/v1/events", json={"events":[e.model_dump(mode="json") for e in events]}).raise_for_status()
    rows = client.get("/v1/incidents").raise_for_status().json()
    reports = []
    for row in rows:
        base = "/v1/incidents/" + row["incident_id"]
        report = client.post(base + "/investigate").raise_for_status().json()
        md = client.get(base + "/report.md").raise_for_status().text
        assert report == client.get(base + "/report").raise_for_status().json()
        for f in report["findings"]:
            for ref in f["evidence"]:
                source, = client.get("/v1/events", params={"event_id":ref["event_id"]}).raise_for_status().json()
                assert source["run_id"] == row["run_id"] and all(source[k] == v for k,v in ref.items())
                assert ref["event_id"] in md
        if row["run_id"] == str(events[0].run_id):
            assert report["verdict"] == "possible_contributor"
        else:
            assert report["verdict"] == "needs_more_telemetry"
        if row["category"] == "verification_failed":
            assert any(f["role"] == "security_policy" for f in report["findings"])
        reports.append(report)
    assert len(reports) == 5
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explore", action="store_true", help="keep the temporary collector and reports open")
    args = parser.parse_args()
    admin = secrets.token_urlsafe(32)
    with TemporaryDirectory() as folder, patch.dict(os.environ, AGENTSHIELD_AUTO_CATEGORIES=""), local_service(Path(folder)/"db", admin) as url:
        with httpx.Client(base_url=url, trust_env=False, timeout=60) as client:
            registration = client.post("/v1/apps", json={"app_id":"week-four-demo"},
                                       headers={"Authorization":"Bearer "+admin}).raise_for_status()
            client.headers["Authorization"] = "Bearer "+registration.json()["api_key"]
            for report in exercise(client):
                print(report["category"], report["verdict"], report["ranking"], len(report["findings"]), "findings")
            if args.explore:
                client.get("/explorer/").raise_for_status()
                print(f"Explorer {url}/explorer/\nTemporary demo app key {registration.json()['api_key']}")
                input("Press Enter to stop the disposable collector. ")
