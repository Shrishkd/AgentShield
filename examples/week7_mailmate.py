"""Drive real MailMate verification and webhook code through in-memory transports."""
import os
from time import time_ns

import httpx
from mailmate import sentence_check
from mailmate.config import N8nSettings
from mailmate.n8n import N8nClient, N8nError

from agentshield import Telemetry
from integrations.mailmate.adapter import instrument
from integrations.mailmate.execution import project_execution


def exercise(sdk):
    attempts = []

    def gateway(request):
        if request.url.path == "/webhook/MailMate-personalize":
            return httpx.Response(200, json={"sentence": "", "sources": []})
        assert request.url.path == "/webhook/MailMate-reply"
        started = time_ns() // 1_000_000
        with httpx.Client(transport=httpx.MockTransport(
                lambda _: httpx.Response(503, json={"error": "private fixture"}))) as gmail:
            response = gmail.post("https://gmail.invalid/drafts")
        assert response.status_code == 503
        attempts.append({"executionStatus": "error", "startTime": started,
                         "executionTime": max(0, time_ns() // 1_000_000 - started),
                         "error": {"message": "private fixture"}})
        return httpx.Response(500, text="private fixture")

    client = N8nClient(N8nSettings("https://workflow.invalid", "synthetic-token"),
                       transport=httpx.MockTransport(gateway), retry_delays=())
    try:
        with instrument(sdk):
            result = sentence_check.personalize_checked(client.personalize, {"company": "Synthetic Company"})
            assert result.attempts and result.attempts[0].problems and not result.sentence
            try:
                client.save_reply_draft("synthetic-thread", "fixture@example.invalid")
            except N8nError:
                pass
            else:
                raise AssertionError("mock draft failure did not reach MailMate")
        assert len(attempts) == 1
        execution = {"id": str(attempts[0]["startTime"]), "data": {"resultData": {
            "runData": {"Create draft": attempts}}}}
        node, = project_execution(sdk.app_id, execution, "mock-gateway")
        assert sdk.emit_event(**node.model_dump(exclude={"app_id", "schema_version"}))
    finally:
        client.close()


if __name__ == "__main__":
    sdk = Telemetry("mailmate", os.environ["AGENTSHIELD_APP_KEY"], os.environ["AGENTSHIELD_URL"])
    try:
        exercise(sdk)
    finally:
        assert sdk.close(10)
        assert bool(sdk.dropped) == (os.getenv("WEEK7_EXPECT_DROPS") == "1")
