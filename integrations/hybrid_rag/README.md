# Week 2 · Hybrid RAG and the run explorer

The adapter runs inside a dedicated RAG server process. It wraps the existing FastAPI ASGI app, graph node factory, citation verifier and optional cross-encoder reranker during the server lifespan. FastAPI's thread pool and LangGraph propagate the SDK context. The response carries an `X-AgentShield-Run-Id` header for looking up that request. The original app launcher remains available.

## Run the acceptance check

Start from the AgentShield root. The demo requires the RAG checkout and its installed Python dependencies, plus local Ollama tags `embeddinggemma` and `llama3.2:1b`. Verify the two tags on the Ollama endpoint you intend to use. Here the RAG environment contains Python 3.11 packages but its launcher points to an old interpreter path. These commands use the installed Python 3.11 interpreter with those packages:

```powershell
$shield = (Get-Location).Path
$env:RAG_ROOT = 'D:\PROJECTS\Advance RAG CHATBOT'
$env:PYTHONPATH = "$shield;$shield\sdk;$env:RAG_ROOT\.venv\Lib\site-packages;$env:RAG_ROOT"
$env:OLLAMA_HOST = 'http://127.0.0.1:11434'
ollama list
python3.11 -B -m pytest tests/test_hybrid_rag.py -q -s
```

On this workstation the default Ollama instance listed no models. The two tags were available under `D:\ollama-models`. To use that model directory on a separate loopback port, run the following in another PowerShell terminal. Then set `$env:OLLAMA_HOST = 'http://127.0.0.1:11490'` in the test terminal, confirm the tags with `ollama list` and run the test. Stop this extra server with Ctrl+C when done.

```powershell
$env:OLLAMA_MODELS = 'D:\ollama-models'
$env:OLLAMA_HOST = '127.0.0.1:11490'
$env:OLLAMA_NO_CLOUD = '1'
ollama serve
```

The test runs `python3.11 -B -m examples.week2`. With no app key set, it starts a disposable collector. It copies the RAG configs into a temporary directory, selects the local generator and creates one synthetic corpus passage, fresh embeddings, checkpoint and response cache. It invokes the actual FastAPI route via its ASGI test client. Retrieval uses HybridRetriever and its real dense and BM25 paths. The second request temporarily makes the retriever raise inside the isolated demo process.

**Exit checks**

- The first request returns an answer and sources. The collector links request, retrieval, generation and citation events under one trace. The completion span shares the request-start span ID.
- The failed request returns HTTP 502. The same run contains the failed retrieval and failed request, and generation never starts.
- With the collector offline, the application returns the same cached answer and sources on a fresh session.

## Inspect the traces in the dashboard

In a clean PowerShell session in AgentShield, build the frontend with Node 24 and start the persistent collector using the root README. Build first so FastAPI can mount the assets:

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
.\.venv\Scripts\python -m pytest -q
```

Use the README registration command with `{"app_id":"hybrid-rag"}` for its JSON body. Keep the returned app key in `$env:AGENTSHIELD_APP_KEY`. For an existing registration, use its saved key. In the same shell set the RAG paths and Ollama host as above, then run:

```powershell
$env:AGENTSHIELD_APP_ID = 'hybrid-rag'
$env:AGENTSHIELD_URL = 'http://127.0.0.1:8765'
python3.11 -B -m examples.week2
```

Open [Run explorer](http://127.0.0.1:8765/explorer/) and connect with the **application key**. Select a run, filter by observed status, and expand any timeline row for event, parent and trace IDs. Durations use a common scale for the displayed page. Refresh fetches newly stored events. Disconnecting or reloading clears the key.

## Monitor the normal application

With the same environment variables, launch from the RAG directory to preserve its relative config and data paths:

```powershell
Push-Location $env:RAG_ROOT
try {
    python3.11 -B -m uvicorn integrations.hybrid_rag.adapter:create_app --factory --host 127.0.0.1 --port 8000
} finally {
    Pop-Location
}
```

The ordinary app's model choice, corpus, guards, response cache and checkpoint behavior apply. Its checked-in config selects a cloud generator. The acceptance demo explicitly selects a local generator in its private config. Stop the monitored server with Ctrl+C. Run the ordinary `src.serve.api:app` entry point to bypass the adapter.

## Interpretation and limits

- `GET /v1/runs` lists the authenticated application's runs, newest observation first. Optional `status`, `limit` and `offset` filter and page the list. `GET /v1/runs/{run_id}` returns a summary and chronological event page. An error anywhere in the run sets its observed status to error. Missing spans after dropped telemetry can leave an incomplete timeline.
- Citation attributes record whether the answer cites anything and whether cited IDs resolve. They do not score semantic support. The current graph omits reranking, so the hook records a span only when a real reranker executes.
- Prompts, retrieved texts, answers, session IDs and exception messages stay outside AgentShield. The verifier exercises actual app code on a small corpus and local model. It does not measure textbook answer quality or production throughput.
- The adapter targets the inspected synchronous node-factory API. Run the opt-in test when updating the RAG checkout. The normal collector suite skips that test unless `RAG_ROOT` is set. Clear `PYTHONPATH` and `RAG_ROOT` in a separate shell when running collector-only tests.
## Remember the RAG application key on Windows

Application registration persists in the collector's SQLite database. The server stores a digest and returns the original secret once. From the AgentShield directory, save the existing key once with a masked prompt. The default port below corresponds to the dedicated RAG collector on 8766.

```powershell
.\scripts\rag-key.ps1 Save
```

In each new RAG PowerShell terminal, run the following before starting the adapter. It sets the app ID, key and collector URL in that terminal and its child processes.

```powershell
& 'D:\PROJECTS\AgentShield\scripts\rag-key.ps1' Load
```

For dashboard login, run `.\scripts\rag-key.ps1 Copy` from AgentShield and paste into **Application key**. Add `-Port 8765` to each command if that is your collector port. The helper stores a Windows DPAPI-encrypted credential under `%LOCALAPPDATA%\AgentShield\keys`, scoped to the Windows user and machine. Reuse the same database when restarting the collector. If you replace the database or change its registration, save the corresponding key again.
