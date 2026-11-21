# Week 7 · MailMate and fleet incidents

## Run the three-application demo

From the AgentShield root, use the collector environment from Week 1, Node and the three sibling checkouts with their application dependencies installed.

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m examples.week7 --explore
```

Select **Read-only fleet** at the printed loopback address and enter the temporary viewer key. The incident list shows the originating application. Filter by application, select an incident and expand its evidence. Press Enter in the terminal to close the collector and discard the temporary database. Omit `--explore` for a noninteractive acceptance check.

The runner expects sibling directories named `Advance RAG CHATBOT`, `Monday` and `MailMate`. Override their locations with `RAG_ROOT`, `MONDAY_ROOT` and `MAILMATE_ROOT`. The corresponding `*_PYTHON` variable selects an interpreter. Windows RAG defaults to Python 3.11 with its checkout's existing 3.11 site-packages. Each child gets a temporary working directory and its own application key.

| Application | What executes | Required incident evidence |
| --- | --- | --- |
| Advanced Hybrid RAG | Existing FastAPI chat route and compiled LangGraph, with an in-memory checkpoint and a retriever that raises | `retrieve` |
| MONDAY | Existing multi-agent fixture with scripted Ollama responses | `check_bullet` |
| MailMate | Existing personalization checks and n8n client with in-memory webhook and Gmail transports | `sentence_verdict`, `webhook.save_reply_draft`, `n8n.create_draft` |

The exit check requires incidents from all three app IDs in one fleet query and opens each application's evidence. MailMate rejects an empty personalization and propagates a simulated draft-service failure. Its isolated transport accepts only the personalization and reply routes. Zero messages are sent. The runner also checks the stored MailMate events for fixture identifiers, text, addresses and credentials.

## Monitor an existing MailMate process

Register the `mailmate` application with the collector as described in the root README. From AgentShield, make the SDK and adapter available to the normal MailMate interpreter. Set `AGENTSHIELD_APP_KEY` to the registration key in that process's environment.

```powershell
$shield = (Get-Location).Path
$env:PYTHONPATH = "$shield;$shield\sdk"
$env:AGENTSHIELD_APP_ID = 'mailmate'
$env:AGENTSHIELD_URL = 'http://127.0.0.1:8765'
Push-Location ..\MailMate
try { .\.venv\Scripts\python -m integrations.mailmate } finally { Pop-Location }
```

This starts the ordinary Streamlit application with its normal configuration and approval gates. Use the three-app demo above for synthetic activity. The launcher installs the observer before Streamlit loads MailMate's pages. Verification exports the boolean verdict. Webhook spans export operation, duration and outcome. Exceptions still reach MailMate. A collector outage can drop observations while the original call completes normally.

## Import n8n execution evidence

The adapter reads a completed execution with expanded `data.resultData.runData`. From the AgentShield root, use the MailMate application key and collector URL set above.

```powershell
.\.venv\Scripts\python -m integrations.mailmate.execution .\execution.json --instance local-n8n
```

Use a stable label per n8n installation. Reimporting the same completed execution produces the same event IDs. The adapter exports recognized node names as fixed operation labels, timestamps, duration and failure flags. Node inputs, Gmail data, error messages and headers stay out of telemetry. Each imported execution has its own trace. The Python webhook trace and imported trace are separate because MailMate's API does not return a trustworthy execution correlation ID.

The default demo projects the mock gateway's observed failure into n8n execution-log format. To try the native engine separately, import [the manual fixture](../integrations/mailmate/mock-draft.workflow.json) into a disposable n8n instance and execute it once. Its manual trigger leads to a Stop And Error node named **Create draft**. Supply the resulting expanded execution JSON to the importer. The fixture has no credentials or network nodes. Execution summaries and compact internal database exports require expansion first. Native runtime compatibility and OTel export depend on the installed n8n instance and were not verified here.

## Persistent fleet access

Set `AGENTSHIELD_VIEWER_KEY` to a distinct, randomly generated secret of at least 32 characters before starting the collector. A restart picks up a rotated key. Leaving it unset disables fleet access. The bearer grants read access to registered application IDs and sanitized incident evidence across every tenant in this local database.

| Route | Result |
| --- | --- |
| `GET /v1/fleet/apps` | Registered application IDs |
| `GET /v1/fleet/incidents` | Paginated incident list with optional app_id, category, severity and status |
| `GET /v1/fleet/incidents/{id}?app_id=mailmate` | Incident and paginated evidence for the selected app |

The application credential continues to scope ingestion, runs and investigations to its own application. The administrator credential controls registration and review. Fleet credentials grant no mutation permissions. Week 7 measures integration behavior on synthetic cases. Benchmark results belong to Week 8.
