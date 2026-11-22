from collections import Counter
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from evals.benchmark import DIGEST, Case, Decision, load_suite, single_agent, summarize
from evals.audit import facts

RESULTS = Path(__file__).resolve().parents[1] / "evals" / "results"


@pytest.mark.parametrize("trial", [1, 2])
def test_published_measurements_recompute(trial):
    measured = json.loads((RESULTS / f"v1-trial-{trial}.json").read_bytes())
    cases = load_suite()
    rows = measured["observations"]
    assert measured["suite_sha256"] == DIGEST
    assert [row["id"] for row in rows] == [c.id for c in cases]
    assert measured["model"]["name"] == "llama3.2:1b"
    assert measured["summary"] == summarize(cases, rows, measured["model"])


def test_frozen_suite_and_labels():
    cases = load_suite()
    assert Counter(c.group for c in cases) == dict(rag=25, monday=25, mailmate=20, policy=15, control=15)
    assert Counter(c.source for c in cases) == {"synthetic": 97, "recorded-demo": 3}
    bad = cases[0].model_dump()
    bad["causes"] = [len(bad["events"])]
    with pytest.raises(ValidationError):
        Case.model_validate(bad)
    with pytest.raises(ValidationError):
        Decision(causes=["1"], blocked_event=None)


def test_misses_wrong_labels_and_clean_false_alarms():
    cases = load_suite()
    known = next(c for c in cases if c.causes)
    clean = next(c for c in cases if not c.labels)
    blocked = next(c for c in cases if c.blocked)
    rows = [dict(id=c.id, accepted=True, ingest_ms=1, detected=[], modes={}) for c in (known,clean,blocked)]
    rows[1]["detected"] = ["execution_error"]
    result = summarize(cases, rows, None)
    assert result["clean_false_alarms"]["rate"] == 1
    assert result["precision"]["rate"] == result["recall"]["rate"] == 0
    graph = result["modes"]["graph"]
    assert graph["top1"] == graph["policy_attempt_recall"] == {"count": 0, "of": 1, "rate": 0}
    assert graph["evidence_label_agreement"]["rate"] is None


def test_factual_audit_checks_statement_and_exact_reference():
    case = load_suite()[0]
    e = case.events[1]
    ref = {k: str(getattr(e, k)) for k in ("event_id", "trace_id", "span_id")}
    finding = {"role": "triage", "confidence": "observed", "evidence": [ref],
               "statement": "A tool boundary reported an exception."}
    report = {"findings": [finding, {**finding, "statement": "The application reported a timeout."},
                           {**finding, "evidence": [{**ref, "trace_id": "missing"}]}]}
    assert [f["supported"] for f in facts(case, report)] == [True, False, False]


@pytest.mark.parametrize("indices", [[0], [-1], [1000], [0, 0]])
def test_single_agent_receives_only_observations_and_rejects_invalid_indices(indices):
    case = load_suite()[0]

    def reply(request):
        prompt = json.loads(json.loads(request.content)["prompt"])
        assert set(prompt) == {"category", "anchors", "events"}
        assert all(set(e) == {"operation","parent","status","checks","ms","end","kind"} for e in prompt["events"])
        return httpx.Response(200, json={"response": json.dumps({"causes": indices, "blocked_event": None})})

    with httpx.Client(base_url="http://127.0.0.1", transport=httpx.MockTransport(reply)) as client:
        if indices == [0]:
            assert single_agent(client, "test", case.events, case.focus, [2]).causes == [0]
        else:
            with pytest.raises(ValueError, match="invalid evidence index"):
                single_agent(client, "test", case.events, case.focus, [2])
