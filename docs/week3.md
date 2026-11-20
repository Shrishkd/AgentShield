# Week 3 · Detection and incident lifecycle

From the AgentShield root, use the Python environment from the main README. The demo uses a temporary SQLite database and a local HTTP server. It needs no RAG checkout, Ollama instance or model download.

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m examples.week3
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
.\.venv\Scripts\python -m examples.week3 --explore
```

The interactive command prints a loopback URL and temporary app key. Connect, choose **Incidents**, filter by category, severity or status, then expand an evidence event to inspect its trace and span IDs. Press Enter in the terminal to stop the server and discard the demo database.

**Acceptance check.** Nine seeded failures must each produce the expected category and source event. Three controls must produce zero incidents. Replaying the same batch must preserve the counts and evidence. Pytest also covers batch rollback, app isolation, late events, lifecycle authorization, database upgrades and SDK exception redaction.

## Rules

| Category | Required observation | Severity |
| --- | --- | --- |
| Invalid schema | Application reports `schema_valid=false` | High |
| Timeout | `timed_out=true`. SDK spans recognize Python and httpx timeout exceptions. | High |
| Tool exception | `tool_exception=true`. SDK tool spans record this when an ordinary exception escapes. | High |
| Verification failed | `claim_verified=false`. This records a verifier rejection. | Medium |
| Missing citation | `has_citations=false` plus `citation_required=true` or an operation configured to require citations | Medium |
| Invalid citation | `citation_ids_valid=false` or `citation_supported=false` | Medium |
| Slow span | A finished span exceeds its operation's configured duration limit | Medium |
| Retry limit | `retry_count` exceeds the configured maximum | Medium |
| Execution error | Application reports error status without a specific failure classification | High |

Missing attributes represent unknowns. Configure mandatory citations only for operations where every answer requires them, accounting for deliberate refusals. The RAG citation-ID check measures whether identifiers resolve. Semantic support requires a separate validator signal.

## Per-app policy

Set `AGENTSHIELD_POLICIES` to the path of a JSON file before starting the collector. Keys are registered application IDs. This example uses illustrative thresholds:

```json
{
  "hybrid-rag": {
    "latency_ms": {"retrieve": 1500},
    "max_retries": 2,
    "require_citations_on": []
  }
}
```

Latency, retry and operation-specific citation requirements default to disabled. Equality to a numeric limit is allowed. Restart the collector to load edits. On the first Week 3 startup the collector scans existing events once, using the active policy and whatever sanitized attributes those events retained. Subsequent policy edits apply to future events. Large databases may take longer on that first startup.

## Storage and lifecycle

Event ingestion, rule evaluation and evidence writes share one SQLite transaction. Events matching the same category in one application and run contribute to a single incident. Separate runs have separate incidents. One event can support multiple rules. Each evidence record retains its original event ID and the reason recorded at ingestion, including any threshold used.

Application bearer keys can read `GET /v1/incidents` and `GET /v1/incidents/{incident_id}`. Both endpoints accept `limit` and `offset`. The list supports `category`, `severity` and `status`. The detail returns a page of sanitized evidence alongside the total evidence count.

Incidents start open. A human using the local administrator credential can acknowledge, resolve or reopen an incident. Additional evidence preserves the chosen state. For a persistent collector, set these variables in its administrator shell:

```powershell
$base = 'http://127.0.0.1:8765'
$id = Read-Host 'Incident ID'
Invoke-RestMethod "$base/v1/incidents/$id" -Method Patch -ContentType 'application/json' -Headers @{Authorization="Bearer $env:AGENTSHIELD_ADMIN_KEY"} -Body '{"status":"acknowledged"}'
```

Week 3 records symptoms. A failed inner tool and an outer request error can yield two categories for the same run. Correlating causes across those observations belongs to the investigation milestone. These seeded checks establish rule behavior only. Production accuracy, MONDAY integration and repair verification remain unmeasured.
