"""Replay the frozen Week 8 suite through a fresh local collector for each trial."""
import argparse
import hashlib
import json
import math
import os
import platform
import secrets
from collections import Counter
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Literal
from unittest.mock import patch

import httpx
from pydantic import Field, StrictInt, model_validator

from agentshield.contracts import Contract, Event
from backend import verifier
from examples.week1 import local_service

SUITE = Path(__file__).with_name("cases-v1.jsonl")
DIGEST = "ffe288991dcd54ec08425414b987139807cfe1e72ef479c057e015586b2361df"
POLICY = {"max_retries": 2, "latency_ms": {"index_lookup": 250}}


class Case(Contract):
    id: str
    group: Literal["rag", "monday", "mailmate", "policy", "control"]
    source: Literal["synthetic", "recorded-demo"]
    events: list[Event] = Field(min_length=1, max_length=32)
    labels: dict[str, list[int]]
    focus: str | None
    causes: list[int]
    blocked: bool

    @model_validator(mode="after")
    def check_labels(self):
        refs = self.causes + [i for indices in self.labels.values() for i in indices]
        if any(i < 0 or i >= len(self.events) for i in refs):
            raise ValueError("label references an absent event")
        if self.focus not in self.labels and (self.focus is not None or self.labels):
            raise ValueError("invalid focal incident")
        if len({(e.run_id, e.trace_id, e.app_id) for e in self.events}) != 1:
            raise ValueError("mixed case identities")
        return self


def load_suite():
    # Canonical LF accommodates Windows checkouts.
    data = SUITE.read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(data).hexdigest() != DIGEST:
        raise ValueError("v1 evaluation corpus checksum mismatch")
    cases = [Case.model_validate_json(line) for line in data.splitlines()]
    if len(cases) != 100 or len({c.id for c in cases}) != 100 or len({c.events[0].run_id for c in cases}) != 100:
        raise ValueError("expected 100 unique cases and runs")
    return cases


class Decision(Contract):
    causes: list[StrictInt] = Field(max_length=3)
    blocked_event: StrictInt | None


def single_agent(client, model, events, category, anchor_indices):
    # Same bounded trace, category and anchor evidence as the graph. No labels or case names.
    spans = {e.span_id: i for i, e in enumerate(events)}
    observations = [{"operation": e.operation, "parent": spans.get(e.parent_span_id), "status": e.status,
                     "checks": e.attributes, "ms": e.duration_ms, "end": e.timestamp.isoformat(),
                     "kind": e.event_type} for e in events]
    prompt = {"category": category, "anchors": anchor_indices, "events": observations}
    body = {"model": model, "stream": False, "format": Decision.model_json_schema(),
            "system": "Review this sanitized incident. Return up to three zero-based child event indices "
                      "in order of likely contribution to an enclosing failed span. Return [] when unknown. "
                      "Return the index of an explicitly blocked action or null. No tools.",
            "prompt": json.dumps(prompt), "options": {"temperature": 0, "seed": 8, "num_predict": 80, "num_ctx": 4096}}
    with client.stream("POST", "/api/generate", json=body) as response:
        response.raise_for_status()
        payload = bytearray()
        for chunk in response.iter_bytes():
            if len(chunk) + len(payload) > 16384:
                raise ValueError("oversized model response")
            payload.extend(chunk)
    data = json.loads(payload)
    if not isinstance(data, dict) or not isinstance(data.get("response"), str):
        raise ValueError("invalid model response")
    decision = Decision.model_validate_json(data["response"])
    refs = decision.causes + ([] if decision.blocked_event is None else [decision.blocked_event])
    if any(i < 0 or i >= len(events) for i in refs) or len(set(decision.causes)) != len(decision.causes):
        raise ValueError("invalid evidence index")
    return decision


def trial(cases, work, model, llm, *, audit=None):
    policy = work / "policy.json"
    apps = {c.events[0].app_id for c in cases}
    policy.write_text(json.dumps(dict.fromkeys(apps, POLICY)))
    admin = secrets.token_urlsafe(32)
    rows = []
    with patch.dict(os.environ, AGENTSHIELD_POLICIES=str(policy), AGENTSHIELD_RANK_MODEL=model or "",
                    AGENTSHIELD_AUTO_CATEGORIES="", AGENTSHIELD_VIEWER_KEY=""), local_service(work/"db", admin) as url, httpx.Client(
                    base_url=url, trust_env=False, timeout=60) as api:
        keys = {app: api.post("/v1/apps", headers={"Authorization": "Bearer " + admin},
                              json={"app_id": app}).raise_for_status().json()["api_key"] for app in apps}
        for index, case in enumerate(cases):
            if index and index % 25 == 0:
                print(f"Evaluated {index}/{len(cases)} cases in this trial", flush=True)
            events = case.events
            api.headers["Authorization"] = "Bearer " + keys[events[0].app_id]
            batch = {"events": [e.model_dump(mode="json") for e in events]}
            start = perf_counter()
            response = api.post("/v1/events", json=batch)
            elapsed = (perf_counter()-start)*1000
            row = {"id": case.id, "ingest_ms": elapsed, "accepted": response.is_success,
                   "detected": [], "modes": {}}
            rows.append(row)
            if not response.is_success:
                continue
            # The collector lists incidents newest first; fixture timestamps can be historical.
            offset = 0
            found = []
            while True:
                page = api.get("/v1/incidents", params={"limit": 100, "offset": offset}).raise_for_status().json()
                found.extend(i for i in page if i["run_id"] == str(events[0].run_id))
                if len(page) < 100:
                    break
                offset += 100
            row["detected"] = sorted(i["category"] for i in found)
            focal = next((i for i in found if i["category"] == case.focus), None)
            if not focal:
                continue
            base = "/v1/incidents/" + focal["incident_id"]
            start = perf_counter()
            detail = api.get(base).raise_for_status().json()
            indices = {str(e.event_id): i for i, e in enumerate(events)}
            anchors = [indices[item["event"]["event_id"]] for item in detail["evidence"]]
            blocked = next((i for i in anchors if events[i].attributes.get("restricted_action_blocked")), None)
            def record(mode, causes, block, calls, supported, total, error=None):
                row["modes"][mode] = dict(causes=causes, blocked=block, calls=calls, supported=supported,
                                          findings=total, error=error, ms=(perf_counter()-start)*1000)
            gold = set(case.labels[case.focus])
            record("rules", [], blocked, 0, int(anchors[0] in gold), 1)
            if model:
                start = perf_counter()
                try:
                    answer = single_agent(llm, model, events, case.focus, anchors)
                    block = answer.blocked_event
                    supported = sum(i in case.causes for i in answer.causes) + int(
                        block is not None and events[block].attributes.get("restricted_action_blocked") is True)
                    record("single", answer.causes, block, 1, supported, len(answer.causes)+int(block is not None))
                except (ValueError, KeyError, httpx.HTTPError) as exc:
                    record("single", [], None, 1, 0, 0, type(exc).__name__)
            start = perf_counter()
            report = api.post(base+"/investigate").raise_for_status().json()
            if audit is not None:
                audit(case, report)
            causes, block, supported = [], None, 0
            for finding in report["findings"]:
                refs = [indices.get(ref["event_id"]) for ref in finding["evidence"]]
                valid = all(i is not None and str(events[i].span_id) == ref["span_id"] and
                            str(events[i].trace_id) == ref["trace_id"] for i, ref in zip(refs, finding["evidence"]))
                if not valid:
                    continue
                first = refs[0]
                if finding["role"] == "root_cause":
                    causes.append(first)
                    supported += int(first in case.causes and len(refs) == 2 and
                                     events[first].parent_span_id == events[refs[1]].span_id)
                elif "blocking a restricted action" in finding["statement"]:
                    block = first
                    supported += int(events[first].attributes.get("restricted_action_blocked") is True)
                else:
                    supported += int(first in gold)
            record("graph", causes, block, report["model_attempts"] or 0, supported, len(report["findings"]),
                   "partial" if report["model_attempts"] is None else None)
    return rows


def ratio(n, d):
    return {"count": n, "of": d, "rate": round(n/d, 4) if d else None}


def summarize(cases, rows, model):
    lookup = {c.id: c for c in cases}
    tp = fp = fn = clean = alarms = 0
    for r in rows:
        truth, pred = set(lookup[r["id"]].labels), set(r["detected"])
        tp += len(truth & pred)
        fp += len(pred - truth)
        fn += len(truth - pred)
        clean += int(not truth)
        alarms += int(not truth and bool(pred))
    modes = {}
    for mode in (["rules", "single", "graph"] if model else ["rules", "graph"]):
        totals = Counter()
        times = []
        for r in rows:
            c = lookup[r["id"]]
            m = r["modes"].get(mode, {})
            predictions = m.get("causes", [])
            if c.causes:
                totals["known"] += 1
                for k in (1, 3):
                    totals[k] += bool(set(predictions[:k]) & set(c.causes))
            if c.focus and not c.causes:
                totals["unknown"] += 1
                totals["abstained"] += bool(m and not m["error"] and not predictions)
            totals["blocked"] += c.blocked
            idx = m.get("blocked")
            totals["reported_blocks"] += idx is not None
            totals["caught"] += bool(c.blocked and idx is not None and c.events[idx].attributes.get("restricted_action_blocked"))
            if m:
                times.append(m["ms"])
                totals.update({k: m[k] for k in ("calls","supported","findings")})
                totals["errors"] += bool(m["error"])
        modes[mode] = {"top1": ratio(totals[1], totals["known"]), "top3": ratio(totals[3], totals["known"]),
                       "unknown_abstentions": ratio(totals["abstained"], totals["unknown"]),
                       "policy_attempt_recall": ratio(totals["caught"], totals["blocked"]),
                       "policy_attempt_precision": ratio(totals["caught"], totals["reported_blocks"]),
                       "evidence_label_agreement": ratio(totals["supported"], totals["findings"]),
                       "investigations": len(times), "errors": totals["errors"], "model_calls": totals["calls"],
                       "median_ms": round(median(times), 2) if times else None}
    latencies = sorted(r["ingest_ms"] for r in rows)
    return {"batch_acceptance": ratio(sum(r["accepted"] for r in rows), len(rows)), "precision": ratio(tp,tp+fp),
            "recall": ratio(tp,tp+fn), "clean_false_alarms": ratio(alarms,clean),
            "ingest_p95_ms": round(latencies[math.ceil(.95*len(rows))-1], 2), "modes": modes}


def replay():
    results = []
    for tag in (601, 602):
        fixture, _, digest = verifier.bundle(tag)
        result = verifier.verify(tag, digest, verifier.Change(setting=fixture.setting, before=False, after=True))
        results.append(result.model_dump(mode="json"))
    controls = [c for r in results for c in r["cases"] if c["kind"] == "control" and c["baseline"] == c["expected"]]
    return {"verified": ratio(sum(r["verdict"] == "passed" for r in results), len(results)),
            "regressions": ratio(sum(c["candidate"] != c["expected"] for c in controls), len(controls)), "results": results}


def trial_summaries(cases, rows, model):
    return [summarize(cases, [r for r in rows if r["trial"] == index], model)
            for index in sorted({r["trial"] for r in rows})]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--model", choices=["llama3.2:1b", "llama3.2:3b"])
    parser.add_argument("--output", type=Path, default=Path(".agentshield/week8.json"))
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("trials must be positive")
    cases = load_suite()
    port = int(os.getenv("AGENTSHIELD_OLLAMA_PORT", "11434"))
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=35) as llm:
        metadata = None
        if args.model:
            tags = llm.get("/api/tags").raise_for_status().json()["models"]
            metadata = next((t for t in tags if t["name"] == args.model), None)
            if not metadata:
                parser.error("requested local model is unavailable")
        raw = []
        for i in range(args.trials):
            with TemporaryDirectory() as folder:
                observations = trial(cases, Path(folder), args.model, llm)
                raw.extend({"trial": i + 1, **row} for row in observations)
            print(f"Finished trial {i+1}/{args.trials}", flush=True)
    result = {"suite_sha256": DIGEST, "trials": args.trials, "cases": len(cases),
              "model": metadata, "python": platform.python_version(), "platform": platform.system(),
              "external_model_fee_usd": 0, "local_compute_cost_usd": None,
              "summary": summarize(cases, raw, args.model), "by_trial": trial_summaries(cases, raw, args.model),
              "replay": replay(), "observations": raw}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
