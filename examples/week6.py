"""Disposable, local-only repair review using synthetic incidents and catalogue fixtures."""
import argparse
import os
import secrets
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import httpx

from agentshield import Event
from examples.week1 import local_service


def proposals(client):
    for operation, attrs in (
        ("citation_ids", {"citation_ids_valid": False, "replay_fixture_id": 601}),
        ("fixture_output_audit", {"claim_escaped": True, "replay_fixture_id": 602}),
        ("citation_ids", {"citation_ids_valid": False}),
    ):
        event = Event(schema_version="1.0", app_id="week-six-demo", run_id=uuid4(), trace_id=uuid4(),
                      span_id=uuid4(), operation=operation, event_type="validation", status="error",
                      attributes=attrs)
        client.post("/v1/events", json={"events": [event.model_dump(mode="json")]}).raise_for_status()
    rows = client.get("/v1/incidents").raise_for_status().json()
    assert len(rows) == 3
    for row in rows:
        base = "/v1/incidents/" + row["incident_id"]
        report = client.post(base + "/investigate").raise_for_status().json()
        review = client.post(base + "/proposals", params={"investigation_id": report["investigation_id"]}).raise_for_status().json()
        assert review["decision"] is None and review["verification_status"] == "unverified"
        yield review["proposal"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explore", action="store_true", help="leave replay authorization and decisions to the reviewer")
    args = parser.parse_args()
    admin = secrets.token_urlsafe(32)
    reviewer = {"Authorization": "Bearer "+admin}
    with TemporaryDirectory() as tmp, patch.dict(os.environ, AGENTSHIELD_AUTO_CATEGORIES="", AGENTSHIELD_RANK_MODEL="",
                                                  AGENTSHIELD_POLICIES=""), local_service(Path(tmp)/"db", admin) as url:
        with httpx.Client(base_url=url, trust_env=False, timeout=45) as client:
            reg = client.post("/v1/apps", headers=reviewer, json={"app_id": "week-six-demo"}).raise_for_status()
            key = reg.json()["api_key"]
            client.headers["Authorization"] = "Bearer "+key
            for proposal in proposals(client):
                if args.explore:
                    continue
                base = "/v1/proposals/"+proposal["proposal_id"]
                authorization = {"request_id": str(uuid4()), "bundle_digest": proposal["bundle_digest"]}
                replay = client.post(base+"/verify", headers=reviewer, json=authorization).raise_for_status().json()
                result = replay["result"]
                assert result["verdict"] == ("passed" if proposal["fixture_id"] else "unverified")
                assert client.get(base).raise_for_status().json()["decision"] is None
                print(proposal["change"]["setting"], proposal["fixture_id"], result["verdict"], len(result["cases"]), "cases")
            if args.explore:
                client.get("/explorer/").raise_for_status()
                print(f"Explorer {url}/explorer/\nTemporary application key {key}\nTemporary administrator key {admin}")
                input("Press Enter to discard the demo. ")


if __name__ == "__main__":
    main()
