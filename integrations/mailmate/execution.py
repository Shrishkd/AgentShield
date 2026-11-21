"""Project a completed, expanded n8n execution into retry-safe AgentShield events."""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import BaseModel, Field

from agentshield import Event


class NodeRun(BaseModel):
    startTime: int = Field(strict=True, ge=0)
    executionTime: int = Field(strict=True, ge=0)
    executionStatus: str | None = None


NODES = {"Webhook": "webhook", "Gmail Send": "gmail_send", "Get thread": "get_thread",
         "Build draft": "build_draft", "Create draft": "create_draft",
         "Respond to Webhook": "respond", "Check request": "check_request",
         "Labelled threads": "labelled_threads", "Find replies": "find_replies"}


def project_execution(app_id, execution, instance):
    """Only finalized node attempts with recognized names become spans."""
    ident = execution["id"]
    if not instance or type(ident) not in (str, int) or not str(ident):
        raise ValueError("instance and execution ID required")
    run = uuid5(NAMESPACE_URL, json.dumps(["n8n", app_id, instance, str(ident)]))
    trace = uuid5(run, "trace")
    for name, attempts in execution["data"]["resultData"]["runData"].items():
        if name not in NODES or not isinstance(attempts, list):
            continue
        for index, raw in enumerate(attempts):
            node = NodeRun.model_validate(raw)
            failed = raw.get("error") is not None or node.executionStatus in {"error", "crashed"}
            if not failed and node.executionStatus not in {None, "success"}:
                continue
            span = uuid5(run, f"{name}/{index}")
            completed = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
                milliseconds=node.startTime + node.executionTime)
            yield Event(schema_version="1.0", app_id=app_id, event_id=uuid5(span, "finish"),
                        run_id=run, trace_id=trace, span_id=span, operation="n8n." + NODES[name],
                        timestamp=completed, duration_ms=float(node.executionTime),
                        status="error" if failed else "ok", event_type="span_finished",
                        attributes={"tool_exception": True} if failed else {})


def upload(client, events):
    count = 0
    remaining = iter(events)
    while batch := list(islice(remaining, 100)):
        client.post("/v1/events", json={"events": [e.model_dump(mode="json") for e in batch]}).raise_for_status()
        count += len(batch)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--instance", required=True, help="stable, non-secret local instance label")
    args = parser.parse_args()
    url = os.getenv("AGENTSHIELD_URL", "http://127.0.0.1:8765")
    headers = {"Authorization": "Bearer " + os.environ["AGENTSHIELD_APP_KEY"]}
    try:
        execution = json.loads(args.file.read_text(encoding="utf-8-sig"))
        events = project_execution(os.getenv("AGENTSHIELD_APP_ID", "mailmate"), execution, args.instance)
        with httpx.Client(base_url=url, headers=headers, trust_env=False) as client:
            count = upload(client, events)
    except httpx.HTTPError:
        raise SystemExit("Collector rejected or could not receive the export; retry the same completed execution") from None
    except (ValueError, KeyError, AttributeError, OverflowError, TypeError):
        raise SystemExit("Invalid execution export; expected expanded data.resultData.runData") from None
    print(f"Exported {count} node observations")


if __name__ == "__main__":
    main()
