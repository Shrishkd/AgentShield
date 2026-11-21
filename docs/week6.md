# Week 6 · Repair proposals and safe replay

From the AgentShield root, update the collector environment and build the dashboard. Restart any persistent collector after updating.

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pytest tests/test_repairs.py -q
.\.venv\Scripts\python -m examples.week6
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
.\.venv\Scripts\python -m examples.week6 --explore
```

The automated demo uses a disposable administrator credential to simulate replay authorization. It leaves every proposal undecided. Interactive mode stops after preparing proposals. Open the printed loopback URL with the temporary **application key**, choose **Incidents**, and open **Repair review** inside an investigation. Inspect the setting, risk, evidence and fixture digest. Enter the temporary **administrator key** and select **Authorize fixture replay**. Review the labelled outcomes before separately accepting or rejecting the proposal. The browser clears the administrator key on use. Press Enter in the terminal to remove the demo database.

## What gets verified

| Incident signal | Proposed fixture setting | Target cases | Regression controls |
| --- | --- | --- | --- |
| Invalid citation IDs, fixture 601 | Enable one corrected answer, then abstain if its IDs still fail validation | Unknown ID corrected, mixed IDs still invalid, empty correction | Valid cited answer, optional uncited answer |
| Unsupported claim found in returned output, fixture 602 | Restore source wording when a bullet uses unsupported skill terms | Unlisted skill, empty support set | Faithful single-skill and multi-skill rewrites |

The versioned JSON fixtures contain synthetic source IDs, normalized skill terms, frozen retry outputs and expected actions. The verifier runs both configurations on identical inputs. Every target must fail at baseline and pass with the candidate. Every control must pass both ways. The third demo incident lacks a fixture binding and must stay **unverified**.

These are proposed adapter policies. The `before=false` value describes the frozen baseline. The verifier does not inspect the installed applications' configuration. The fixtures exercise policy decisions on normalized inputs. End-to-end model generation, extraction of skills from natural language, semantic citation quality, latency and production improvement require different evaluations.

## Permissions and isolation

- The deterministic LangGraph repair-planning node receives only the report and its authenticated source events. Its catalogue allows two boolean settings. The planner has no shell, deployment, filesystem or model tools. Unsupported evidence yields a manual-review proposal with no change.
- A supported category alone cannot bind a fixture. A cited event must carry both the relevant validator signal and its matching `replay_fixture_id`. That ID is an application assertion about the fixture. A passing run establishes behavior on the named fixture only.
- The independent verifier starts the pinned Wasmtime runtime in a short-lived Python process. The worker receives bounded integers through stdin and runs a fixed, import-free WebAssembly module. It has no WASI instance, host callbacks, guest memory, credentials, filesystem handles, or network functions available to the guest. The host shim reads its bundled module in an empty working directory with only required Windows platform variables inherited.
- Each collector admits one replay worker at a time, with at most 16 cases, 10,000 Wasm fuel units and five seconds of wall time per replay. Guest threads and parallel compilation are disabled. The bundle digest covers fixture contents, module source, worker source and the pinned runtime version. Missing fixtures, changed bundles, sandbox traps, unavailable runtimes or wall-time exhaustion return **unverified**. A request authorizing the wrong digest receives HTTP 409.
- Replays and decisions require the administrator bearer credential. The app credential can request a proposal and inspect its results. Acceptance must identify the proposal's passing verification. Rejection is also available before replay. The terminal decision records the credential role, UTC timestamp, proposal and optional verification ID. This local MVP has one administrator identity. No decision writes a patch, changes application configuration, merges or deploys.

## API and persistence

| Request | Credential | Purpose |
| --- | --- | --- |
| `POST /v1/incidents/{id}/proposals?investigation_id={uuid}` | App | Plan once per immutable investigation version |
| `GET /v1/incidents/{id}/proposal?investigation_id={uuid}` | App | Load the proposal associated with that report |
| `GET /v1/proposals/{id}` | App | Read the proposal, latest result and decision |
| `POST /v1/proposals/{id}/verify` | Administrator | Authorize replay with a UUID `request_id` and the displayed `bundle_digest` |
| `GET /v1/proposals/{id}/verifications/{verification_id}` | App | Retrieve a recorded verification by ID |
| `POST /v1/proposals/{id}/decision` | Administrator | Record `choice` as `accept` or `reject`, supplying `verification_id` for acceptance |

Existing SQLite databases gain the three repair tables on startup. An exact replay request can be retried safely. After an unverified attempt, use a fresh request UUID to retry a restored runtime. A terminal verification is reused for that proposal. A changed bundle cannot verify an earlier proposal against new fixture contents. A conflicting decision returns HTTP 409. Restarting the collector preserves the evidence, verification and decision.
