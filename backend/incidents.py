import json
import sqlite3
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import AwareDatetime, Field, TypeAdapter

from agentshield.contracts import Contract, Event, Label

Category = Literal["invalid_schema", "timeout", "tool_exception", "verification_failed",
                   "missing_citation", "invalid_citation", "slow_span", "retry_limit", "execution_error"]
Severity = Literal["high", "medium"]
State = Literal["open", "acknowledged", "resolved"]


class Policy(Contract):
    latency_ms: dict[Label, Annotated[float, Field(gt=0, strict=True)]] = Field(default_factory=dict)
    max_retries: int | None = Field(None, ge=0, strict=True)
    require_citations_on: set[Label] = Field(default_factory=set)


def load_policies(path):
    if not path:
        return {}
    return TypeAdapter(dict[Label, Policy]).validate_json(Path(path).read_bytes())


class Incident(Contract):
    incident_id: UUID
    app_id: Label
    run_id: UUID
    category: Category
    severity: Severity
    status: State
    first_seen: AwareDatetime
    last_seen: AwareDatetime
    evidence_count: int


class Evidence(Contract):
    reason: str
    event: Event


class Detail(Contract):
    incident: Incident
    evidence: list[Evidence]


class Transition(Contract):
    status: State


def matches(event, policy):
    a = event.attributes
    if a.get("schema_valid") is False:
        yield "invalid_schema", "high", "The application reported schema_valid=false."
    if a.get("timed_out") is True:
        yield "timeout", "high", "The application reported a timeout."
    if a.get("tool_exception") is True:
        yield "tool_exception", "high", "A tool boundary reported an exception."
    if a.get("claim_verified") is False:
        yield "verification_failed", "medium", "The verifier rejected a claim."
    if a.get("citation_ids_valid") is False or a.get("citation_supported") is False:
        yield "invalid_citation", "medium", "The application reported a failed citation check."
    required = a.get("citation_required") is True or event.operation in policy.require_citations_on
    if required and a.get("has_citations") is False:
        yield "missing_citation", "medium", "Citations were required and the check found none."
    limit = policy.latency_ms.get(event.operation)
    if event.event_type == "span_finished" and limit is not None and event.duration_ms is not None and event.duration_ms > limit:
        yield "slow_span", "medium", f"Duration exceeded the configured {limit:g} ms limit."
    if policy.max_retries is not None and a.get("retry_count", 0) > policy.max_retries:
        yield "retry_limit", "medium", f"Retry count exceeded the configured maximum of {policy.max_retries}."


def detect(db, event, policy):
    rules = list(matches(event, policy))
    if event.status == "error" and all(category in {"slow_span", "retry_limit"} for category, _, _ in rules):
        rules.append(("execution_error", "high", "The application reported error status."))
    at = event.timestamp.isoformat(timespec="microseconds")
    for category, severity, reason in rules:
        incident_id, = db.execute("""
            INSERT INTO incidents VALUES (?,?,?,?,?,'open',?,?,0)
            ON CONFLICT(app_id,run_id,category) DO UPDATE SET
                first_seen=min(first_seen,excluded.first_seen), last_seen=max(last_seen,excluded.last_seen)
            RETURNING incident_id
        """, (str(uuid4()), event.app_id, str(event.run_id), category, severity, at, at)).fetchone()
        added = db.execute("INSERT OR IGNORE INTO incident_evidence VALUES (?,?,?,?)",
                           (incident_id, event.app_id, str(event.event_id), reason))
        if added.rowcount:
            db.execute("UPDATE incidents SET evidence_count=evidence_count+1 WHERE incident_id=?", (incident_id,))


DEFAULT_POLICY = Policy()


def initialize(db, policies):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS incidents (
          incident_id TEXT PRIMARY KEY, app_id TEXT NOT NULL REFERENCES applications(id), run_id TEXT NOT NULL,
          category TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL,
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, evidence_count INTEGER NOT NULL,
          UNIQUE(app_id,run_id,category));
        CREATE INDEX IF NOT EXISTS incidents_recent ON incidents(app_id,last_seen DESC);
        CREATE TABLE IF NOT EXISTS incident_evidence (
          incident_id TEXT NOT NULL REFERENCES incidents, app_id TEXT NOT NULL, event_id TEXT NOT NULL,
          reason TEXT NOT NULL, PRIMARY KEY(incident_id,event_id),
          FOREIGN KEY(app_id,event_id) REFERENCES events(app,id));
        CREATE TABLE IF NOT EXISTS detector_migrations (version INTEGER PRIMARY KEY);
    """)
    db.execute("BEGIN IMMEDIATE")
    if db.execute("INSERT OR IGNORE INTO detector_migrations VALUES (1)").rowcount:
        for payload, in db.execute("SELECT payload FROM events"):
            event = Event.model_validate_json(payload)
            detect(db, event, policies.get(event.app_id, DEFAULT_POLICY))


def listing(db, app, category, severity, status, limit, offset):
    db.row_factory = sqlite3.Row
    clauses = ["app_id=?"]
    params = [app]
    for column, value in (("category", category), ("severity", severity), ("status", status)):
        if value is not None:
            clauses.append(column + "=?")
            params.append(value)
    query = "SELECT * FROM incidents WHERE " + " AND ".join(clauses)
    return [dict(row) for row in db.execute(query+" ORDER BY last_seen DESC,incident_id LIMIT ? OFFSET ?",
                                            (*params, limit, offset))]


def detail(db, app, incident_id, limit, offset):
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM incidents WHERE app_id=? AND incident_id=?", (app, incident_id)).fetchone()
    if row is None:
        raise HTTPException(404, "incident not found")
    evidence = db.execute("""
        SELECT reason,payload FROM incident_evidence x JOIN events e ON e.app=x.app_id AND e.id=x.event_id
        WHERE x.incident_id=? AND x.app_id=? ORDER BY e.timestamp,e.id LIMIT ? OFFSET ?
    """, (incident_id, app, limit, offset))
    return {"incident": dict(row), "evidence": [{"reason": r, "event": json.loads(p)} for r, p in evidence]}
