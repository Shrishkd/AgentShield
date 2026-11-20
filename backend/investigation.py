"""Bounded investigation inputs, evidence references and durable reports."""
import hashlib
import json
import time
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import Field

from agentshield.contracts import Contract, Event
from backend import incidents


class Snapshot(Contract):
    incident: incidents.Incident
    anchors: list[incidents.Evidence]
    context: list[Event]
    run_event_count: int


class Reference(Contract):
    event_id: UUID
    trace_id: UUID
    span_id: UUID

    @classmethod
    def of(cls, event):
        return cls(event_id=event.event_id, trace_id=event.trace_id, span_id=event.span_id)


class Finding(Contract):
    role: Literal["triage", "root_cause", "security_policy"]
    confidence: Literal["observed", "possible"]
    statement: str
    evidence: list[Reference] = Field(min_length=1)


class Report(Contract):
    investigation_id: UUID
    incident_id: UUID
    app_id: str
    run_id: UUID
    category: incidents.Category
    severity: incidents.Severity
    verdict: Literal["possible_contributor", "needs_more_telemetry"]
    findings: list[Finding]
    limitations: list[str]
    next_step: str
    model_attempts: int | None
    ranking: Literal["evidence_order", "ollama"]


def markdown(report):
    lines = ["# Incident investigation", "", f"Investigation `{report.investigation_id}`",
             f"Incident `{report.incident_id}` · Application `{report.app_id}` · [Run {report.run_id}](/v1/runs/{report.run_id})",
             "", f"**{report.category} · {report.severity} · {report.verdict}**", ""]
    for finding in report.findings:
        lines.extend([f"## {finding.role.replace('_', ' ').title()} ({finding.confidence})", "",
                      finding.statement, ""])
        for ref in finding.evidence:
            lines.append(f"- [Event {ref.event_id}](/v1/events?event_id={ref.event_id}) · trace `{ref.trace_id}` · span `{ref.span_id}`")
        lines.append("")
    lines.extend(["## Limitations", "", *["- "+item for item in report.limitations],
                  "", "## Next step", "", report.next_step, ""])
    return "\n".join(lines)


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS investigations (
        id TEXT PRIMARY KEY, incident TEXT NOT NULL REFERENCES incidents(incident_id),
        signature TEXT NOT NULL, created REAL NOT NULL, lease_until REAL NOT NULL,
        owner TEXT, report TEXT, graph_attempts INTEGER NOT NULL DEFAULT 1, UNIQUE(incident,signature))""")
    db.execute("""CREATE TABLE IF NOT EXISTS investigation_queue (
        incident TEXT PRIMARY KEY REFERENCES incidents(incident_id), category TEXT NOT NULL,
        generation INTEGER NOT NULL DEFAULT 1, available REAL NOT NULL DEFAULT 0)""")


def enqueue(db, detected, categories):
    for category, incident_id in detected:
        if category in categories:
            db.execute("""INSERT INTO investigation_queue(incident,category) VALUES (?,?)
                          ON CONFLICT(incident) DO UPDATE SET generation=generation+1""", (incident_id, category))


def work_once(database, categories, run_investigation):
    placeholders = ",".join("?" for _ in categories)
    with database() as db:
        job = db.execute(f"""SELECT q.incident,i.app_id,q.generation FROM investigation_queue q
            JOIN incidents i ON i.incident_id=q.incident WHERE q.available<=? AND q.category IN ({placeholders})
            ORDER BY q.available,q.rowid LIMIT 1""", (time.time(), *categories)).fetchone()
    if job is None:
        return False
    incident_id, app, generation = job
    try:
        run_investigation(incident_id, app)
    except HTTPException as error:
        if error.status_code not in {409,429,503}:
            raise
        delay = int((error.headers or {}).get("Retry-After", "1"))
        with database() as db:
            db.execute("UPDATE investigation_queue SET available=? WHERE incident=?", (time.time()+delay, incident_id))
    else:
        with database() as db:
            db.execute("DELETE FROM investigation_queue WHERE incident=? AND generation=?", (incident_id,generation))
    return True


def acquire(db, app_id, incident_id, model):
    db.execute("BEGIN IMMEDIATE")
    detail = incidents.Detail.model_validate(incidents.detail(db, app_id, incident_id, 16, 0))
    params = (app_id, str(detail.incident.run_id))
    total, = db.execute("SELECT count(*) FROM events WHERE app=? AND run=?", params).fetchone()
    rows = db.execute("""SELECT payload FROM events WHERE app=? AND run=?
                         ORDER BY timestamp DESC,id DESC LIMIT 32""", params)
    snapshot = Snapshot(incident=detail.incident, anchors=detail.evidence,
                        context=[Event.model_validate_json(row[0]) for row in rows], run_event_count=total)
    signature = hashlib.sha256(json.dumps([1, model, snapshot.model_dump(mode="json")],
                                          sort_keys=True).encode()).hexdigest()
    now = time.time()
    row = db.execute("SELECT id,lease_until,report,graph_attempts FROM investigations WHERE incident=? AND signature=?",
                     (incident_id, signature)).fetchone()
    if row and row[2]:
        return row[0], None, snapshot, Report.model_validate_json(row[2]), 0
    if db.execute("SELECT 1 FROM investigations WHERE incident=? AND lease_until>?", (incident_id, now)).fetchone():
        raise HTTPException(409, "investigation in progress", headers={"Retry-After":"2"})
    token = str(uuid4())
    if row:
        db.execute("UPDATE investigations SET lease_until=?,owner=?,graph_attempts=graph_attempts+1 WHERE id=?",
                   (now+90, token, row[0]))
        return row[0], token, snapshot, None, row[3]+1
    count, = db.execute("SELECT count(*) FROM investigations WHERE incident=? AND created>?",
                        (incident_id, now-60)).fetchone()
    if count >= 3:
        raise HTTPException(429, "investigation budget reached", headers={"Retry-After":"60"})
    ident = str(uuid4())
    db.execute("INSERT INTO investigations(id,incident,signature,created,lease_until,owner) VALUES (?,?,?,?,?,?)",
               (ident, incident_id, signature, now, now+90, token))
    return ident, token, snapshot, None, 1


def partial_report(ident, snap):
    findings = [Finding(role="triage", confidence="observed", statement=a.reason, evidence=[Reference.of(a.event)])
                for a in snap.anchors[:1]]
    return Report(investigation_id=ident, incident_id=snap.incident.incident_id, run_id=snap.incident.run_id,
                  app_id=snap.incident.app_id, category=snap.incident.category, severity=snap.incident.severity,
                  verdict="needs_more_telemetry", findings=findings, ranking="evidence_order", model_attempts=None,
                  limitations=["The graph execution budget was exhausted. Only detector evidence is available."],
                  next_step="Review the source evidence and check the local investigator service.")


def read_report(db, app, incident_id, investigation_id=None):
    query = """SELECT report FROM investigations JOIN incidents ON incidents.incident_id=investigations.incident
               WHERE app_id=? AND incident=? AND report IS NOT NULL"""
    params = [app, incident_id]
    if investigation_id:
        query += " AND investigations.id=?"
        params.append(str(investigation_id))
    row = db.execute(query+" ORDER BY created DESC LIMIT 1", params).fetchone()
    if not row:
        raise HTTPException(404, "report not found")
    return Report.model_validate_json(row[0])
