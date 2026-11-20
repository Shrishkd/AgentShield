# Week 4 · Incident investigation

## Run locally

From the AgentShield root, update the collector environment. If the persistent collector is already running, restart it after installing dependencies and building the dashboard.

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m examples.week4
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
.\.venv\Scripts\python -m examples.week4 --explore
```

The demo starts a disposable loopback collector and creates five incidents from synthetic telemetry. It investigates each and verifies that every cited event, trace and span resolves in the owning application. With `--explore`, use the printed URL and temporary application key. Select **Incidents**, open an incident and expand a finding. **Download Markdown** exports the displayed report version. Enter in the terminal stops the demo and removes its database and checkpoints.

**Exit checks.** The propagated request failure has two possible contributors with parent and child references. The ambiguous request error returns `needs_more_telemetry`. A rejected claim triggers policy review and leaves downstream publication unknown. Invalid model rankings fall back to evidence order. Tests cover these branches, interrupted execution, deadlines, duplicate requests, retry budgets, version retrieval and app isolation.

## What the graph can conclude

1. Triage reads the detector category and severity and cites a representative triggering event.
2. The root-cause node checks for an explicit child timeout or tool exception and an enclosing failed span. Both spans must share a trace and run, with compatible completion times and a direct parent reference. At least one must be incident evidence. The node returns up to three possible contributors. Each cites both events.
3. Security/policy review runs for schema, citation or claim-validation incidents and for incident evidence with `restricted_action_blocked=true`. It reports observed checks and distinguishes rejection from downstream execution. This flag supplements telemetry for existing incidents.

The graph reads up to 16 triggering events and the newest 32 events in the run. A report warns when the available telemetry exceeds that snapshot. Unlinked exceptions, missing spans, upstream outages and actual downstream effects can require operator investigation. There are no repair or deployment tools.

## Optional local model ranking

The default graph runs offline in deterministic evidence order. To rank competing supported hypotheses, start a local Ollama service with an installed `llama3.2:1b` or `llama3.2:3b` tag, then set the collector environment:

```powershell
$env:AGENTSHIELD_RANK_MODEL = 'llama3.2:1b'
$env:AGENTSHIELD_OLLAMA_PORT = '11434'
.\.venv\Scripts\python -m examples.week4
```

The endpoint is fixed to loopback. Use a local Ollama daemon with cloud disabled for an offline deployment. Only the root-cause node can call it, and only when at least two candidates exist. Its prompt contains candidate indices, numeric timings and allowlisted checks. The response must be a permutation of existing indices. A model cannot add prose, invent a cause, change a citation or invoke tools. Reports identify the ranking method. Local inference can time out on a cold model.

## API and recovery

Use the existing application bearer key. On-demand investigations run outside the ingestion transaction. To investigate selected detector outputs automatically, set the category list before starting the collector:

```powershell
$env:AGENTSHIELD_AUTO_CATEGORIES = 'timeout,tool_exception,verification_failed'
```

The default list is empty. For enabled categories, each newly detected event queues its incident in the event transaction. A single worker coalesces pending evidence for the same incident and investigates after commit. Pending work survives a collector restart and shares the on-demand cache, lease and rate limits. Earlier incidents can be investigated from the dashboard. The synthetic demo uses on-demand mode in its disposable collector.

| Request | Result |
| --- | --- |
| `POST /v1/incidents/{id}/investigate` | Investigate the current snapshot or return its stored result |
| `GET /v1/incidents/{id}/report` | Latest completed Pydantic JSON report |
| `GET /v1/incidents/{id}/report.md` | Markdown report with authenticated run and event links |

Both reads accept `?investigation_id=<uuid>` to retrieve a specific completed version. The collector keeps reports in its existing SQLite database and LangGraph checkpoints beside it in `<database>.checkpoints`. Keep both on restart. The SDK and monitored applications do not need LangGraph for telemetry export.

A snapshot and model configuration identify a report version. Repeating a completed request reuses it. New observations can create a new version. Each incident permits one active investigation and three new versions per minute. An overlapping request returns HTTP 409, and an exhausted rate budget returns 429. A transient graph interruption returns 503 on its first attempt. Retry the same request to resume the last committed node. After an abrupt process exit, the lease expires in 90 seconds. Two unsuccessful graph attempts exhaust the execution budget and yield a detector-only report.

Each graph invocation has a 30-second deadline and a five-step recursion cap. The model has two attempts per root-cause node execution, eight seconds each, a 2,048-token context, and a 64-token output limit. Deadline exhaustion returns a partial report. A crash during a model call may repeat that node on recovery. Timeout cancellation bounds the collector's wait; Ollama controls its own inference process. These checks measure report grounding on fixtures. Production diagnosis accuracy remains unmeasured.
