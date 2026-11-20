# AgentShield

**Week 4 adds checkpointed LangGraph investigations.** Open an incident to investigate its evidence, inspect possible contributors, or download a Markdown report. The [Week 4 runbook](docs/week4.md) covers the demo, optional local Ollama ranking, operating limits and recovery.

**Week 3 adds deterministic incidents and an incident explorer.** Run the seeded demo, configure per-app thresholds, or review the lifecycle API using the [week 3 runbook](docs/week3.md).

**Week 2 adds the Advanced Hybrid RAG adapter and local React run explorer.** Build the UI before starting the collector. Follow the [week 2 runbook](integrations/hybrid_rag/README.md) for the live-model demo, real application launcher, tests and limitations.

Week 1 implements local telemetry ingestion. A Python application records spans or individual checks. The SDK validates and redacts events, puts them in a bounded queue, and exports batches on a background thread. FastAPI authenticates each application and stores its sanitized events in SQLite. An application can read its own events by run, trace and status.

| Directory | Responsibility |
| --- | --- |
| `sdk/agentshield` | Installable Python SDK, event contract, redaction and exporter |
| `backend` | Local service, app credentials and immutable event storage |
| `tests`, `examples` | Contract and failure-path tests, live synthetic demo |
| `integrations` | Advanced Hybrid RAG adapter and MONDAY integration notes |
| `frontend`, `evals` | React run explorer and reserved benchmark directory |

## Run the week 1 acceptance check

From the repository root in PowerShell, using Python 3.12:

```powershell
python3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m examples.week1
```

The demo starts a loopback HTTP server on an available port, generates a temporary administrator key, registers a synthetic app and uses the SDK to send 100 events. It checks that the collector returns 100 unique IDs, finds all 10 seeded errors and accepts a replay with zero new rows. It removes its temporary database at exit. The counts measure ingestion behavior only.

## Start a persistent collector

```powershell
$env:AGENTSHIELD_ADMIN_KEY = .\.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(32))"
$collector = Start-Process .\.venv\Scripts\python -ArgumentList '-m','uvicorn','backend.app:create_app','--factory','--host','127.0.0.1','--port','8765','--no-access-log' -WindowStyle Hidden -PassThru
```

The service stores data under `.agentshield/events.sqlite3`. Set `AGENTSHIELD_DB` before startup to choose another path. Restarting preserves events and app keys. When the service is listening, register an application in the same PowerShell session. Keep the administrator key out of application code and shell history. Stop this service later with `$collector | Stop-Process`.

```powershell
$base = 'http://127.0.0.1:8765'
$app = Invoke-RestMethod "$base/v1/apps" -Method Post -ContentType 'application/json' -Headers @{Authorization="Bearer $env:AGENTSHIELD_ADMIN_KEY"} -Body '{"app_id":"local-demo"}'
$env:AGENTSHIELD_APP_KEY = $app.api_key
```

Registration returns a new high-entropy key once. SQLite stores its SHA-256 digest. Duplicate registration returns HTTP 409. For separate Python applications install the SDK with their interpreter using `python -m pip install -e D:\PROJECTS\AgentShield\sdk`.

```python
import os
from agentshield import Telemetry

telemetry = Telemetry("local-demo", os.environ["AGENTSHIELD_APP_KEY"])
try:
    with telemetry.span("request"):
        with telemetry.span("retrieve"):
            result = ["synthetic document"]
finally:
    telemetry.close()
```

```powershell
Invoke-RestMethod "$base/v1/events?status=ok&limit=20" -Headers @{Authorization="Bearer $env:AGENTSHIELD_APP_KEY"}
```

## Contract and boundaries

- `POST /v1/events` accepts an object with an `events` array of 1–100 records and a 128 KiB request limit. OpenAPI at `/docs` describes all fields. Each wire record carries version `1.0`, app ID, UUID event/run/trace/span IDs, an aware timestamp, operation, event type and status. The SDK creates event IDs and timestamps. Optional fields carry parent span, duration and attributes. Storage normalizes timestamps to UTC.
- Use developer-defined, static operation and application labels. The SDK and server independently retain only boolean `citation_supported`, `claim_verified`, `schema_valid` and bounded integer `retrieved_count`, `retry_count` attributes. Other attributes and mistyped values are discarded. Never encode personal information in identifiers or labels. Raw prompts, model responses and exception messages have no schema fields.
- App credentials scope reads and writes. Exact retries are idempotent within an app. Reusing an ID with changed sanitized content rejects the whole batch. Reads support `run_id`, `trace_id`, `status`, `limit` and `offset` in timestamp and event-ID order.
- A full queue or failed export drops telemetry. `emit_event()` returns whether the event was queued. Monitor `dropped` and the bounded `close()` result when completeness matters. Nested spans inherit context across asyncio tasks. The SDK has no disk spool or automatic retry. A hard process exit can lose queued data.
- SQLite and loopback HTTP target a trusted local machine. Protect the database with OS permissions. Key rotation, retention and multi-user access management are future work. Use a single service process for this milestone.
- The week 1 emitter runs without model calls. The week 2 RAG demo uses local Ollama models. The week 4 graph can investigate offline or use local Ollama to rank competing hypotheses. Repair, replay and benchmarking remain later milestones.
