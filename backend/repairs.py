"""Evidence-bound proposal planning and the local human review API."""
from datetime import datetime, timezone
from threading import BoundedSemaphore
from typing import Literal, TypedDict
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException
from pydantic import AwareDatetime, Field

from agentshield.contracts import Contract, Event
from backend import investigation, verifier


class Proposal(Contract):
    proposal_id: UUID
    investigation_id: UUID
    incident_id: UUID
    app_id: str
    evidence: list[investigation.Reference]
    summary: str
    risk: str
    change: verifier.Change | None
    fixture_id: int | None
    bundle_digest: str | None


class Authorization(Contract):
    request_id: UUID
    bundle_digest: str | None = Field(max_length=64)


class Replay(Contract):
    verification_id: UUID
    recorded_at: AwareDatetime
    actor: Literal["local-administrator"]
    result: verifier.Result


class Decision(Contract):
    choice: Literal["accept", "reject"]
    verification_id: UUID | None = None


class RecordedDecision(Decision):
    actor: Literal["local-administrator"]
    recorded_at: AwareDatetime


class Review(Contract):
    proposal: Proposal
    verification_status: Literal["unverified", "passed", "failed"]
    replay: Replay | None
    decision: RecordedDecision | None


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS repair_proposals (
          id TEXT PRIMARY KEY, investigation TEXT UNIQUE NOT NULL REFERENCES investigations(id),
          app TEXT NOT NULL REFERENCES applications(id), data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS repair_replays (
          id TEXT NOT NULL, proposal TEXT NOT NULL REFERENCES repair_proposals(id), data TEXT NOT NULL,
          PRIMARY KEY(proposal,id));
        CREATE TABLE IF NOT EXISTS repair_decisions (
          proposal TEXT PRIMARY KEY REFERENCES repair_proposals(id), data TEXT NOT NULL);
    """)


def cited_events(db, report):
    refs = {ref.event_id: ref for finding in report.findings for ref in finding.evidence}
    events = []
    for ref in refs.values():
        row = db.execute("SELECT payload FROM events WHERE app=? AND run=? AND id=?",
                         (report.app_id, str(report.run_id), str(ref.event_id))).fetchone()
        if row:
            event = Event.model_validate_json(row[0])
            if event.trace_id == ref.trace_id and event.span_id == ref.span_id:
                events.append(event)
    return events


class PlannerState(TypedDict, total=False):
    report: investigation.Report
    events: list[Event]
    proposal: Proposal


def repair_planner(state: PlannerState):
    report = state["report"]
    change = fixture_id = None
    summary = "Collect a reproducible fixture and review the incident manually."
    risk = "The available evidence does not support a catalogued change."
    relevant = []
    for event in state["events"]:
        if report.category == "invalid_citation" and event.attributes.get("citation_ids_valid") is False:
            setting, tag = "citation_retry", 601
            summary = "Check cited IDs against retrieved sources. Allow one corrected answer, then abstain on invalid IDs."
            risk = "An additional generation can increase latency. Valid IDs alone cannot establish semantic support."
        elif report.category == "escaped_claim" and event.attributes.get("claim_escaped") is True:
            setting, tag = "source_fallback", 602
            summary = "Compare draft skill terms with cited source terms. Replace an unsupported bullet with its source wording."
            risk = "A narrow term list can discard valid paraphrases. Term extraction needs separate application testing."
        else:
            continue
        change = verifier.Change(setting=setting, before=False, after=True)
        relevant.append(investigation.Reference.of(event))
        if event.attributes.get("replay_fixture_id") == tag:
            fixture_id = tag
    evidence = relevant or [investigation.Reference.of(e) for e in state["events"]]
    return {"proposal": Proposal(proposal_id=uuid4(), investigation_id=report.investigation_id,
                                 incident_id=report.incident_id, app_id=report.app_id, evidence=evidence,
                                 summary=summary, risk=risk, change=change, fixture_id=fixture_id, bundle_digest=None)}


def plan(report, events):
    from langgraph.graph import END, START, StateGraph
    from langsmith import tracing_context
    with tracing_context(enabled=False):
        graph = StateGraph(PlannerState)
        graph.add_node("repair_planner", repair_planner)
        graph.add_edge(START, "repair_planner")
        graph.add_edge("repair_planner", END)
        proposal = graph.compile().invoke({"report":report, "events":events}, {"recursion_limit":2})["proposal"]
    if proposal.fixture_id:
        try:
            _, _, proposal.bundle_digest = verifier.bundle(proposal.fixture_id)
        except (OSError, ValueError):
            pass
    return proposal


def read(db, ident, app=None):
    query = "SELECT data FROM repair_proposals WHERE id=?"
    params = [str(ident)]
    if app is not None:
        query += " AND app=?"
        params.append(app)
    row = db.execute(query, params).fetchone()
    if not row:
        raise HTTPException(404, "proposal not found")
    replay = db.execute("SELECT data FROM repair_replays WHERE proposal=? ORDER BY rowid DESC LIMIT 1",
                        (str(ident),)).fetchone()
    decision = db.execute("SELECT data FROM repair_decisions WHERE proposal=?", (str(ident),)).fetchone()
    replay = Replay.model_validate_json(replay[0]) if replay else None
    return Review(proposal=Proposal.model_validate_json(row[0]), replay=replay,
                  verification_status=replay.result.verdict if replay else "unverified",
                  decision=RecordedDecision.model_validate_json(decision[0]) if decision else None)


def routes(api, database, application, administrator):
    worker_slot = BoundedSemaphore(1)

    @api.get("/v1/incidents/{incident_id}/proposal", response_model=Review)
    def current(incident_id: UUID, investigation_id: UUID, app: str = Depends(application)):
        with database() as db:
            db.execute("BEGIN")
            row = db.execute("""SELECT p.id FROM repair_proposals p JOIN investigations i ON i.id=p.investigation
                                WHERE p.app=? AND i.incident=? AND p.investigation=?""",
                             (app, str(incident_id), str(investigation_id))).fetchone()
            if not row:
                raise HTTPException(404, "proposal not found")
            return read(db, row[0], app)

    @api.post("/v1/incidents/{incident_id}/proposals", response_model=Review)
    def propose(incident_id: UUID, investigation_id: UUID, app: str = Depends(application)):
        with database() as db:
            db.execute("BEGIN")
            report = investigation.read_report(db, app, str(incident_id), investigation_id)
            existing = db.execute("SELECT id FROM repair_proposals WHERE investigation=?", (str(investigation_id),)).fetchone()
            if existing:
                return read(db, existing[0], app)
            events = cited_events(db, report)
        proposal = plan(report, events)
        with database() as db:
            db.execute("""INSERT INTO repair_proposals VALUES (?,?,?,?)
                          ON CONFLICT(investigation) DO NOTHING""", (str(proposal.proposal_id),
                       str(investigation_id), app, proposal.model_dump_json()))
            ident, = db.execute("SELECT id FROM repair_proposals WHERE investigation=?", (str(investigation_id),)).fetchone()
            return read(db, ident, app)

    @api.get("/v1/proposals/{ident}", response_model=Review)
    def inspect(ident: UUID, app: str = Depends(application)):
        with database() as db:
            db.execute("BEGIN")
            return read(db, ident, app)

    @api.get("/v1/proposals/{ident}/verifications/{verification_id}", response_model=Replay)
    def verification(ident: UUID, verification_id: UUID, app: str = Depends(application)):
        with database() as db:
            row = db.execute("""SELECT r.data FROM repair_replays r JOIN repair_proposals p ON r.proposal=p.id
                                WHERE p.app=? AND p.id=? AND r.id=?""",
                             (app, str(ident), str(verification_id))).fetchone()
            if not row:
                raise HTTPException(404, "verification not found")
            return Replay.model_validate_json(row[0])

    @api.post("/v1/proposals/{ident}/verify", response_model=Replay, dependencies=[Depends(administrator)])
    def replay(ident: UUID, authorization: Authorization):
        def prior_or_check(db):
            review = read(db, ident)
            if authorization.bundle_digest != review.proposal.bundle_digest:
                raise HTTPException(409, "replay bundle mismatch")
            prior = db.execute("SELECT data FROM repair_replays WHERE proposal=? AND id=?",
                               (str(ident), str(authorization.request_id))).fetchone()
            if prior:
                return review, Replay.model_validate_json(prior[0])
            if review.decision:
                raise HTTPException(409, "proposal review is final")
            return review, review.replay if review.verification_status != "unverified" else None

        with database() as db:
            db.execute("BEGIN")
            review, prior = prior_or_check(db)
        if prior:
            return prior
        p = review.proposal
        if not worker_slot.acquire(blocking=False):
            raise HTTPException(409, "replay worker busy; retry shortly", headers={"Retry-After": "1"})
        try:
            result = verifier.verify(p.fixture_id, p.bundle_digest, p.change)
        finally:
            worker_slot.release()
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            _, prior = prior_or_check(db)
            if prior:
                return prior
            record = Replay(verification_id=authorization.request_id, recorded_at=datetime.now(timezone.utc),
                            actor="local-administrator", result=result)
            db.execute("INSERT INTO repair_replays VALUES (?,?,?)",
                       (str(record.verification_id), str(ident), record.model_dump_json()))
            return record

    @api.post("/v1/proposals/{ident}/decision", response_model=RecordedDecision, dependencies=[Depends(administrator)])
    def decide(ident: UUID, decision: Decision):
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            review = read(db, ident)
            if review.decision:
                if (review.decision.choice, review.decision.verification_id) == (decision.choice, decision.verification_id):
                    return review.decision
                raise HTTPException(409, "proposal review is final")
            if decision.verification_id and (not review.replay or decision.verification_id != review.replay.verification_id):
                raise HTTPException(409, "verification does not match the current proposal")
            if decision.choice == "accept" and (not decision.verification_id or review.verification_status != "passed"):
                raise HTTPException(409, "acceptance requires a passing verification")
            record = RecordedDecision(**decision.model_dump(), actor="local-administrator",
                                      recorded_at=datetime.now(timezone.utc))
            db.execute("INSERT INTO repair_decisions VALUES (?,?)", (str(ident), record.model_dump_json()))
            return record
