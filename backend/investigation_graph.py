import asyncio
import json
import os
from itertools import permutations
from typing import TypedDict

import httpx
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import Field, StrictInt

from agentshield.contracts import Contract
from backend.investigation import Finding, Reference, Report, Snapshot

DEADLINE = 30
MODEL_TIMEOUT = 8


class State(TypedDict, total=False):
    snapshot: dict
    triage: list[dict]
    hypotheses: list[dict]
    policy: list[dict]
    notes: list[str]
    attempts: int
    ranking: str


def finding(role, confidence, statement, *events):
    return Finding(role=role, confidence=confidence, statement=statement,
                   evidence=[Reference.of(e) for e in events]).model_dump(mode="json")


def triage(state):
    snap = Snapshot.model_validate(state["snapshot"])
    observations = [finding("triage", "observed", anchor.reason, anchor.event) for anchor in snap.anchors[:1]]
    return {"triage": observations}


def candidates(snap):
    events = {e.event_id: e for e in snap.context}
    anchors = {a.event.event_id for a in snap.anchors}
    events.update({a.event.event_id: a.event for a in snap.anchors})
    parents = {(e.trace_id, e.span_id): e for e in events.values()
               if e.event_type == "span_finished" and e.status == "error"}
    result = []
    for child in sorted(events.values(), key=lambda e: (e.timestamp, str(e.event_id))):
        parent = parents.get((child.trace_id, child.parent_span_id))
        if (not parent or parent.span_id == child.span_id or parent.timestamp < child.timestamp
                or not anchors.intersection((child.event_id, parent.event_id))):
            continue
        if child.attributes.get("timed_out"):
            signal = "timeout"
        elif child.attributes.get("tool_exception"):
            signal = "tool exception"
        else:
            continue
        result.append(finding("root_cause", "possible",
                              f"The {signal} recorded by {child.operation} may have contributed to "
                              f"the enclosing {parent.operation} failure.", child, parent))
    return result[:3]


class Ranking(Contract):
    order: list[StrictInt] = Field(min_length=1, max_length=3)


async def model_order(model, hypotheses, snap):
    events = {str(e.event_id): e for e in snap.context}
    events.update({str(a.event.event_id): a.event for a in snap.anchors})
    choices = []
    for index, hypothesis in enumerate(hypotheses):
        child, parent = [events[ref["event_id"]] for ref in hypothesis["evidence"]]
        choices.append({"index": index, "child_checks": child.attributes, "child_duration_ms": child.duration_ms,
                        "parent_duration_ms": parent.duration_ms,
                        "ms_before_parent_finished": (parent.timestamp-child.timestamp).total_seconds()*1000})
    prompt = json.dumps(choices)
    schema = Ranking.model_json_schema()
    schema["properties"]["order"]["enum"] = list(permutations(range(len(choices))))
    body = {"model": model, "stream": False, "format": schema,
            "system": "Order candidate indices by strength of evidence for contributing to the parent failure. "
                      "Use the child checks and timings. Return every supplied zero-based index exactly once.",
            "prompt": prompt, "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 64}}
    port = int(os.getenv("AGENTSHIELD_OLLAMA_PORT", "11434"))
    async with httpx.AsyncClient(timeout=MODEL_TIMEOUT, trust_env=False) as client:
        async with client.stream("POST", f"http://127.0.0.1:{port}/api/generate", json=body) as res:
            res.raise_for_status()
            data = bytearray()
            async for chunk in res.aiter_bytes():
                if len(data) + len(chunk) > 16384:
                    raise ValueError("oversized model response")
                data.extend(chunk)
    result = Ranking.model_validate_json(json.loads(data)["response"])
    if sorted(result.order) != list(range(len(hypotheses))):
        raise ValueError("invalid candidate references")
    return result.order


def needs_policy(state):
    snap = Snapshot.model_validate(state["snapshot"])
    return snap.incident.category in {"verification_failed", "escaped_claim", "invalid_schema", "missing_citation", "invalid_citation"} or any(
        a.event.attributes.get("restricted_action_blocked") for a in snap.anchors)


def policy(state):
    snap = Snapshot.model_validate(state["snapshot"])
    findings = []
    def priority(anchor):
        if anchor.event.attributes.get("restricted_action_blocked"):
            return 0
        return 1 if anchor.event.attributes.get("claim_verified") is False else 2

    for anchor in sorted(snap.anchors, key=priority):
        sources = [anchor.event]
        if anchor.event.attributes.get("restricted_action_blocked"):
            statement = "The application reported blocking a restricted action. Its downstream outcome is unknown."
        elif snap.incident.category == "escaped_claim":
            statement = "An independent check flagged the returned draft. Publication or delivery remains unverified."
        elif anchor.event.attributes.get("claim_verified") is False:
            statement = "The claim verifier rejected the claim. Publication or delivery remains unverified."
            agent = next((e for e in snap.context if e.span_id == anchor.event.parent_span_id
                          and e.trace_id == anchor.event.trace_id and e.operation == "resume_tailor"
                          and e.event_type == "span_finished"), None)
            if agent and anchor.event.operation in {"check_bullet","check_summary"}:
                statement = f"MONDAY's {agent.operation} agent rejected a draft claim at {anchor.event.operation}."
                sources.append(agent)
                index = anchor.event.attributes.get("generation_index")
                generations = [e for e in snap.context if e.operation == "structured_generation"
                               and e.event_type == "span_finished" and e.status == "ok"
                               and e.trace_id == agent.trace_id and e.parent_span_id == agent.span_id
                               and e.timestamp <= anchor.event.timestamp
                               and index is not None and e.attributes.get("generation_index") == index]
                if len(generations) == 1:
                    statement += f" The checked output came from generation {index}."
                    sources.append(generations[0])
                statement += " Publication or delivery remains unverified."
        else:
            statement = "The recorded validation signal warrants policy review. A security breach remains unverified."
        findings.append(finding("security_policy", "observed", statement, *sources))
        if len(findings) == 3:
            break
    return {"policy": findings}


def graph(saver, model):
    async def root_cause(state):
        snap = Snapshot.model_validate(state["snapshot"])
        hypotheses = candidates(snap)
        attempts = 0
        notes = []
        ranking = "evidence_order"
        if model and len(hypotheses) > 1:
            for attempts in (1, 2):
                try:
                    order = await asyncio.wait_for(model_order(model, hypotheses, snap), MODEL_TIMEOUT)
                    hypotheses = [hypotheses[i] for i in order]
                    ranking = "ollama"
                    break
                except (TimeoutError, httpx.HTTPError, KeyError, TypeError, ValueError):
                    continue
            if ranking != "ollama":
                notes.append("Local model ranking was unavailable or invalid. Candidates retain evidence order.")
        return {"hypotheses": hypotheses, "notes": notes, "attempts": attempts, "ranking": ranking}

    builder = StateGraph(State)
    builder.add_node("triage", triage)
    builder.add_node("root_cause", root_cause)
    builder.add_node("security_policy", policy)
    builder.add_edge(START, "triage")
    builder.add_edge("triage", "root_cause")
    builder.add_conditional_edges("root_cause", needs_policy, {True:"security_policy", False:END})
    builder.add_edge("security_policy", END)
    return builder.compile(checkpointer=saver)


async def investigate(path, ident, snap, model):
    initial = {"snapshot": snap.model_dump(mode="json")}
    config = {"configurable":{"thread_id":ident}, "recursion_limit":5}
    with tracing_context(enabled=False):
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            workflow = graph(saver, model)
            async def execute():
                saved = await workflow.aget_state(config)
                if saved.values and not saved.next:
                    return saved.values
                return await workflow.ainvoke(None if saved.values else initial, config, durability="sync")

            try:
                state = await asyncio.wait_for(execute(), DEADLINE)
            except TimeoutError:
                state = {**triage(initial), "attempts":None, "notes":["Investigation deadline reached. Partial analysis only."]}
    hypotheses = state.get("hypotheses", [])
    notes = list(state.get("notes", []))
    if snap.incident.evidence_count > len(snap.anchors) or snap.run_event_count > len(snap.context):
        notes.append("The bounded snapshot covers only part of the available telemetry.")
    notes.append("Parent and child failures can be correlated without establishing causation." if hypotheses
                 else "There is insufficient linked evidence to identify a contributing cause.")
    return Report(investigation_id=ident, incident_id=snap.incident.incident_id, app_id=snap.incident.app_id,
                  run_id=snap.incident.run_id, category=snap.incident.category, severity=snap.incident.severity,
                  verdict="possible_contributor" if hypotheses else "needs_more_telemetry",
                  findings=state["triage"] + hypotheses + state.get("policy", []), limitations=notes,
                  next_step="Review the linked spans and check the failing dependency." if hypotheses else
                  "Capture the failing child span, its parent and the validator outcome for human review.",
                  ranking=state.get("ranking", "evidence_order"), model_attempts=state.get("attempts", 0))
