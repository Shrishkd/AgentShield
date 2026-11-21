# Week 5 · MONDAY integration

MONDAY runs in its own Python environment. The opt-in launcher wraps its existing pipeline, agent entry points, structured generator, Ollama calls and deterministic checks. AgentShield receives static operation labels, correlation IDs, booleans and counts. MONDAY keeps its résumé text, job descriptions, contact details, prompts, model replies and validator messages.

## Run the acceptance check

From the AgentShield root in PowerShell, use the collector environment from the main README and a working MONDAY checkout with its own dependencies installed:

```powershell
$env:MONDAY_ROOT = 'D:\PROJECTS\Monday'
.\.venv\Scripts\python -m pytest tests/test_monday.py -q
.\.venv\Scripts\python -m examples.week5
```

The demo discovers a sibling `Monday` checkout by default. Use `--monday-root` for another location or `MONDAY_PYTHON` for another MONDAY interpreter. To inspect the reports in the existing dashboard:

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
.\.venv\Scripts\python -m examples.week5 --explore
```

Use the printed loopback URL and temporary app key, then select **Runs** or **Incidents**. Press Enter in the terminal to discard the demo. Each case has its own synthetic bank/job or tracker state. All use the real job matcher, résumé tailor, Pydantic parser, validators, assembly code and SQLite draft tracker. The Ollama HTTP transport returns scripted responses. The PDF builder is stubbed. The demo never opens the user's tracker, compiles a document, researches a company, or contacts a recruiter.

| Case | Exit check |
| --- | --- |
| Clean draft | A source-supported bullet reaches a pending draft and creates zero incidents. |
| Corrected draft | MONDAY rejects an unsupported Kubernetes claim, retries, and retains the corrected bullet. |
| Source fallback | The correction still contains the claim. MONDAY rejects it again and saves the original fact wording. |
| Escaped claim | MONDAY accepts an untracked Fortran claim. A labelled independent check finds it in the saved pending draft and emits `escaped_claim`. |
| Recovered generation | A transport failure and an invalid JSON response precede a successful response. The final structured span reports two retries. |
| Model switch | The first model returns HTTP 404. MONDAY succeeds with the second model slot. |
| Exhausted generation | Two malformed responses exhaust the configured model choices. MONDAY raises its original error and creates no résumé draft. |

The acceptance script also repeats the source-fallback case with the collector offline. It checks MONDAY's saved content and pending status, and verifies that the exporter counted dropped events.

## Monitor the existing application

Start the persistent collector as described in the main README. Register an application with `{"app_id":"monday"}` and keep its returned application bearer key in `AGENTSHIELD_APP_KEY`. From the AgentShield checkout:

```powershell
$shield = (Get-Location).Path
$env:MONDAY_ROOT = 'D:\PROJECTS\Monday'
$env:AGENTSHIELD_APP_ID = 'monday'
$env:AGENTSHIELD_URL = 'http://127.0.0.1:8765'
$env:PYTHONPATH = "$shield;$shield\sdk;$env:MONDAY_ROOT"
$mondayPython = Join-Path $env:MONDAY_ROOT '.venv\Scripts\python.exe'
& $mondayPython -B -m integrations.monday --help
```

Pass the same arguments you normally give MONDAY after `integrations.monday`. To start its review UI, use `& $mondayPython -B -m integrations.monday --streamlit`. These launchers retain MONDAY's normal configuration and storage. For the isolated exercise, use the acceptance command above. With no app key the launcher runs ordinary MONDAY. Instrumentation startup failures fall back to the ordinary application, and the SDK drops undeliverable events.

## Interpret a trace

- `pipeline.tailor` encloses matching, tailoring and draft creation. A `check_bullet` event with `claim_verified=false` identifies a rejected claim. The policy report cites the validator, its parent agent and the matching structured generation when these events are available in the investigation snapshot. A failed length or structure check uses `validator_passed=false` alone.
- `generation_index` counts structured generations within an agent invocation. `retry_count` counts extra observed Ollama calls within one structured generation, including transport retries, schema correction and model fallback. `model_slot` counts observed model changes starting at one. Numeric slots keep configured model names private. A custom client that bypasses `ollama.Client.chat` can still produce schema observations but lacks per-call transport spans.
- `assembled_draft` records bullet and summary fallback separately. A `verification_failed` incident can coexist with a successful pipeline completion. `escaped_claim` requires an explicit independent output check. Passing MONDAY's validator alone cannot establish that a draft is factually correct. Neither category establishes publication or delivery.
- Hooks cover the inspected job matcher, résumé tailor, outreach, cover letter, interview briefing/preparation and mock interview entry points. The seven-case integration run exercises matching and résumé tailoring. The remaining workflows have no end-to-end Week 5 acceptance claim. Run the integration test after MONDAY updates its internal interfaces.
- The saved-output check recognises two deliberately unsupported fixture terms. General escaped-claim detection, live-model quality metrics, repair proposals and sandbox replay are outside this milestone.
