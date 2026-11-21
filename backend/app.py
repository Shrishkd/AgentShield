import hashlib
import asyncio
import json
import logging
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager, closing, contextmanager
from pathlib import Path
from typing import Annotated, get_args
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime, Field

from agentshield.contracts import Contract, Event, Registration, Status
from backend import incidents, investigation, repairs

MAX_BYTES = 128 * 1024
bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


class ReceivedEvent(Event):
    event_id: UUID
    timestamp: AwareDatetime


class EventBatch(Contract):
    events: list[ReceivedEvent] = Field(min_length=1, max_length=100)


class RunSummary(Contract):
    run_id: UUID
    last_event_at: AwareDatetime
    event_count: int
    status: Status


class RunPage(Contract):
    run: RunSummary
    events: list[ReceivedEvent]


class BodyLimit:
    """Count streamed bytes before FastAPI parses JSON, including chunked requests."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_BYTES:
                response = JSONResponse({"detail": "payload too large"}, status_code=413)
                return await response(scope, receive, send)
            body.extend(chunk)
            if not message.get("more_body"):
                break

        async def bounded_receive():
            nonlocal body
            if body is None:
                return await receive()
            data, body = bytes(body), None
            return {"type": "http.request", "body": data, "more_body": False}

        await self.app(scope, bounded_receive, send)


def digest(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


def create_app(db_path: Path | None = None, admin_key: str | None = None,
               policy_path: Path | None = None) -> FastAPI:
    path = Path(db_path or os.getenv("AGENTSHIELD_DB", ".agentshield/events.sqlite3"))
    admin = admin_key or os.getenv("AGENTSHIELD_ADMIN_KEY")
    viewer = os.getenv("AGENTSHIELD_VIEWER_KEY")
    if viewer and (len(viewer) < 32 or digest(viewer) == digest(admin or "")):
        raise ValueError("Use a distinct fleet viewer key of at least 32 characters")
    policies = incidents.load_policies(policy_path or os.getenv("AGENTSHIELD_POLICIES"))
    model = os.getenv("AGENTSHIELD_RANK_MODEL", "")
    if model not in {"", "llama3.2:1b", "llama3.2:3b"}:
        raise ValueError("Choose a supported local llama3.2 rank model")
    auto_categories = {v.strip() for v in os.getenv("AGENTSHIELD_AUTO_CATEGORIES","").split(",") if v.strip()}
    if auto_categories.difference(get_args(incidents.Category)):
        raise ValueError("Unknown automatic investigation category")

    @contextmanager
    def database():
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    @asynccontextmanager
    async def lifespan(app):
        if not admin or len(admin) < 32:
            raise RuntimeError("Set AGENTSHIELD_ADMIN_KEY to a random key of at least 32 characters")
        path.parent.mkdir(parents=True, exist_ok=True)
        with database() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS applications (
                    id TEXT PRIMARY KEY, key_hash BLOB UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    app TEXT NOT NULL REFERENCES applications(id), id TEXT NOT NULL,
                    run TEXT NOT NULL, trace TEXT NOT NULL, status TEXT NOT NULL,
                    timestamp TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(app,id));
                CREATE INDEX IF NOT EXISTS events_time ON events(app,timestamp,id);
                CREATE INDEX IF NOT EXISTS events_run ON events(app,run,timestamp);
            """)
            if viewer and db.execute("SELECT 1 FROM applications WHERE key_hash=?", (digest(viewer),)).fetchone():
                raise ValueError("Fleet viewer credential is already an application credential")
            incidents.initialize(db, policies)
            investigation.initialize(db)
            repairs.initialize(db)
        stop = asyncio.Event()
        async def process_queue():
            while not stop.is_set():
                try:
                    if await asyncio.to_thread(investigation.work_once, database, auto_categories, run_investigation):
                        continue
                except Exception:
                    logging.getLogger(__name__).warning("Investigation worker deferred a pending job")
                try:
                    await asyncio.wait_for(stop.wait(), 1)
                except TimeoutError:
                    pass
        worker = asyncio.create_task(process_queue()) if auto_categories else None
        try:
            yield
        finally:
            stop.set()
            if worker:
                await worker

    api = FastAPI(title="AgentShield", version="0.1.0", lifespan=lifespan)
    api.add_middleware(BodyLimit)

    @api.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    def application(auth: Credentials) -> str:
        if auth:
            with database() as db:
                row = db.execute("SELECT id FROM applications WHERE key_hash=?",
                                 (digest(auth.credentials),)).fetchone()
                if row:
                    return row[0]
        raise HTTPException(401, "application credential required")

    def administrator(auth: Credentials):
        if not auth or not secrets.compare_digest(digest(auth.credentials), digest(admin)):
            raise HTTPException(401, "administrator credential required")

    def fleet_viewer(auth: Credentials):
        if not viewer or not auth or not secrets.compare_digest(digest(auth.credentials), digest(viewer)):
            raise HTTPException(401, "fleet viewer credential required")

    @api.get("/v1/fleet/apps", response_model=list[str], dependencies=[Depends(fleet_viewer)])
    def fleet_apps():
        with database() as db:
            return [row[0] for row in db.execute("SELECT id FROM applications ORDER BY id")]

    @api.get("/v1/fleet/incidents", response_model=list[incidents.Incident], dependencies=[Depends(fleet_viewer)])
    def fleet_incidents(app_id: str | None = None, category: incidents.Category | None = None,
                        severity: incidents.Severity | None = None, status: incidents.State | None = None,
                        limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        with database() as db:
            return incidents.listing(db, app_id, category, severity, status, limit, offset)

    @api.get("/v1/fleet/incidents/{incident_id}", response_model=incidents.Detail, dependencies=[Depends(fleet_viewer)])
    def fleet_detail(incident_id: UUID, app_id: str, limit: int = Query(100, ge=1, le=100),
                     offset: int = Query(0, ge=0)):
        with database() as db:
            db.execute("BEGIN")
            return incidents.detail(db, app_id, str(incident_id), limit, offset)

    @api.post("/v1/apps", status_code=201, dependencies=[Depends(administrator)])
    def register(registration: Registration):
        key = secrets.token_urlsafe(32)
        try:
            with database() as db:
                db.execute("INSERT INTO applications VALUES (?,?)", (registration.app_id, digest(key)))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "application already registered")
        return JSONResponse({"app_id": registration.app_id, "api_key": key}, status_code=201,
                            headers={"Cache-Control": "no-store"})

    @api.post("/v1/events")
    def ingest(batch: EventBatch, app: str = Depends(application)):
        if any(event.app_id != app for event in batch.events):
            raise HTTPException(403, "application mismatch")
        inserted = 0
        with database() as db:
            for event in batch.events:
                payload = json.dumps(event.model_dump(mode="json"), sort_keys=True)
                ident = str(event.event_id)
                cursor = db.execute(
                    "INSERT INTO events VALUES (?,?,?,?,?,?,?) ON CONFLICT(app,id) DO NOTHING",
                    (app, ident, str(event.run_id), str(event.trace_id), event.status,
                     event.timestamp.isoformat(timespec="microseconds"), payload))
                if cursor.rowcount:
                    inserted += 1
                    detected = incidents.detect(db, event, policies.get(app, incidents.DEFAULT_POLICY))
                    investigation.enqueue(db, detected, auto_categories)
                else:
                    existing, = db.execute("SELECT payload FROM events WHERE app=? AND id=?",
                                           (app, ident)).fetchone()
                    if existing != payload:
                        raise HTTPException(409, "event ID reused with different content")
        return {"inserted": inserted, "duplicates": len(batch.events) - inserted}

    @api.get("/v1/events", response_model=list[ReceivedEvent])
    def events(app: str = Depends(application), run_id: UUID | None = None,
               trace_id: UUID | None = None, status: Status | None = None, event_id: UUID | None = None,
               limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
        clauses = ["app=?"]
        params = [app]
        for column, value in (("run", run_id), ("trace", trace_id), ("status", status), ("id", event_id)):
            if value is not None:
                clauses.append(column + "=?")
                params.append(str(value))
        query = "SELECT payload FROM events WHERE " + " AND ".join(clauses)
        with database() as db:
            rows = db.execute(query + " ORDER BY timestamp,id LIMIT ? OFFSET ?",
                              (*params, limit, offset))
            return [json.loads(payload) for payload, in rows]

    summaries = """
        SELECT run, max(timestamp), count(*),
               CASE WHEN max(status='error') THEN 'error' ELSE 'ok' END AS observed_status
        FROM events WHERE app=?
    """

    def summary(row):
        return RunSummary(run_id=row[0], last_event_at=row[1], event_count=row[2], status=row[3])

    @api.get("/v1/runs", response_model=list[RunSummary])
    def runs(app: str = Depends(application), status: Status | None = None,
             limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        query = summaries + " GROUP BY run"
        params = [app]
        if status:
            query += " HAVING observed_status=?"
            params.append(status)
        with database() as db:
            rows = db.execute(query + " ORDER BY max(timestamp) DESC, run LIMIT ? OFFSET ?",
                              (*params, limit, offset))
            return [summary(row) for row in rows]

    @api.get("/v1/runs/{run_id}", response_model=RunPage)
    def run_detail(run_id: UUID, app: str = Depends(application),
                   limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
        with database() as db:
            db.execute("BEGIN")
            params = (app, str(run_id))
            row = db.execute(summaries + " AND run=? GROUP BY run", params).fetchone()
            if row is None:
                raise HTTPException(404, "run not found")
            records = db.execute(
                "SELECT payload FROM events WHERE app=? AND run=? ORDER BY timestamp,id LIMIT ? OFFSET ?",
                (*params, limit, offset))
            return {"run": summary(row), "events": [json.loads(payload) for payload, in records]}

    @api.get("/v1/incidents", response_model=list[incidents.Incident])
    def list_incidents(app: str = Depends(application), category: incidents.Category | None = None,
                       severity: incidents.Severity | None = None, status: incidents.State | None = None,
                       limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        with database() as db:
            return incidents.listing(db, app, category, severity, status, limit, offset)

    @api.get("/v1/incidents/{incident_id}", response_model=incidents.Detail)
    def get_incident(incident_id: UUID, app: str = Depends(application),
                     limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
        with database() as db:
            db.execute("BEGIN")
            return incidents.detail(db, app, str(incident_id), limit, offset)

    @api.patch("/v1/incidents/{incident_id}", response_model=incidents.Incident,
               dependencies=[Depends(administrator)])
    def update_incident(incident_id: UUID, change: incidents.Transition):
        with database() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("UPDATE incidents SET status=? WHERE incident_id=? RETURNING *",
                             (change.status, str(incident_id))).fetchone()
            if row is None:
                raise HTTPException(404, "incident not found")
            return dict(row)

    def run_investigation(incident_id, app):
        with database() as db:
            ident, token, snapshot, cached, attempt = investigation.acquire(db, app, str(incident_id), model)
        if cached:
            return cached
        try:
            if attempt > 2:
                report = investigation.partial_report(ident, snapshot)
            else:
                try:
                    from backend.investigation_graph import investigate
                    checkpoints = path.with_name(path.name + ".checkpoints")
                    report = asyncio.run(investigate(checkpoints, ident, snapshot, model))
                except Exception as error:
                    if attempt == 1:
                        raise HTTPException(503, "graph interrupted; retry to resume",
                                            headers={"Retry-After":"1"}) from error
                    report = investigation.partial_report(ident, snapshot)
            with database() as db:
                updated = db.execute("UPDATE investigations SET report=?,owner=NULL,lease_until=0 WHERE id=? AND owner=?",
                                     (report.model_dump_json(), ident, token))
                if not updated.rowcount:
                    raise HTTPException(409, "investigation lease expired")
            return report
        finally:
            with database() as db:
                db.execute("UPDATE investigations SET owner=NULL,lease_until=0 WHERE id=? AND owner=?", (ident, token))

    @api.post("/v1/incidents/{incident_id}/investigate", response_model=investigation.Report)
    def investigate_incident(incident_id: UUID, app: str = Depends(application)):
        return run_investigation(incident_id, app)

    @api.get("/v1/incidents/{incident_id}/report", response_model=investigation.Report)
    def incident_report(incident_id: UUID, app: str = Depends(application), investigation_id: UUID | None = None):
        with database() as db:
            return investigation.read_report(db, app, str(incident_id), investigation_id)

    @api.get("/v1/incidents/{incident_id}/report.md", response_class=PlainTextResponse)
    def incident_markdown(incident_id: UUID, app: str = Depends(application), investigation_id: UUID | None = None):
        with database() as db:
            report = investigation.read_report(db, app, str(incident_id), investigation_id)
        return PlainTextResponse(investigation.markdown(report), media_type="text/markdown")

    repairs.routes(api, database, application, administrator)
    dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if (dist / "index.html").is_file():
        api.mount("/explorer", StaticFiles(directory=dist, html=True), name="explorer")
    return api
