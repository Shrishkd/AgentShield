"""Check observed graph statements against the cited frozen trace, independently of ranking."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from evals.benchmark import DIGEST, load_suite, ratio, trial


def supports(event, statement):
    flags = event.attributes
    checks = {
        "The application reported schema_valid=false.": flags.get("schema_valid") is False,
        "The application reported a timeout.": flags.get("timed_out") is True,
        "A tool boundary reported an exception.": flags.get("tool_exception") is True,
        "The application reported error status.": event.status == "error",
        "The verifier rejected a claim.": flags.get("claim_verified") is False,
        "The application validator flagged a draft check.": flags.get("validator_passed") is False,
        "The application reported a failed citation check.": (
            flags.get("citation_ids_valid") is False or flags.get("citation_supported") is False),
        "Citations were required and the check found none.": (
            flags.get("citation_required") is True and flags.get("has_citations") is False),
        "An independent check found an unsupported claim in the returned draft.": flags.get("claim_escaped") is True,
        "Retry count exceeded the configured maximum of 2.": flags.get("retry_count", 0) > 2,
        "Duration exceeded the configured 250 ms limit.": (
            event.operation == "index_lookup" and event.event_type == "span_finished" and
            event.duration_ms is not None and event.duration_ms > 250),
        "The application reported blocking a restricted action. Its downstream outcome is unknown.":
            flags.get("restricted_action_blocked") is True,
        "An independent check flagged the returned draft. Publication or delivery remains unverified.":
            flags.get("claim_escaped") is True,
        "The claim verifier rejected the claim. Publication or delivery remains unverified.":
            flags.get("claim_verified") is False,
        "The recorded validation signal warrants policy review. A security breach remains unverified.": (
            any(flags.get(k) is False for k in ("schema_valid", "validator_passed", "citation_ids_valid", "citation_supported"))
            or (flags.get("citation_required") is True and flags.get("has_citations") is False)),
    }
    return checks.get(statement, False)


def facts(case, report):
    events = {(str(e.event_id), str(e.trace_id), str(e.span_id)): e for e in case.events}
    for finding in report["findings"]:
        if finding["confidence"] != "observed":
            continue
        refs = finding["evidence"]
        cited = [events.get((r["event_id"], r["trace_id"], r["span_id"])) for r in refs]
        # v1's adjudicated factual sentences each make one observation about a single event.
        supported = len(cited) == 1 and cited[0] is not None and supports(cited[0], finding["statement"])
        yield {"case": case.id, "role": finding["role"], "statement": finding["statement"],
               "evidence": refs, "supported": supported}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".agentshield/week8-facts.json"))
    args = parser.parse_args()
    findings = []
    with TemporaryDirectory() as folder:
        trial(load_suite(), Path(folder), None, None, audit=lambda c, r: findings.extend(facts(c, r)))
    summary = ratio(sum(f["supported"] for f in findings), len(findings))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"suite_sha256": DIGEST, "observed_faithfulness": summary,
                                      "findings": findings}, indent=2), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
