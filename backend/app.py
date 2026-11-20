import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager, closing, contextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, Field

from agentshield.contracts import Contract, Event, Registration, Status

MAX_BYTES = 128 * 1024
bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


class ReceivedEvent(Event):
    event_id: UUID
    timestamp: AwareDatetime


class EventBatch(Contract):
    events: list[ReceivedEvent] = Field(min_length=1, max_length=100)


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


def create_app(db_path: Path | None = None, admin_key: str | None = None) -> FastAPI:
    path = Path(db_path or os.getenv("AGENTSHIELD_DB", ".agentshield/events.sqlite3"))
    admin = admin_key or os.getenv("AGENTSHIELD_ADMIN_KEY")

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
        yield

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

    @api.post("/v1/apps", status_code=201)
    def register(registration: Registration, auth: Credentials):
        if not auth or not secrets.compare_digest(digest(auth.credentials), digest(admin)):
            raise HTTPException(401, "administrator credential required")
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
                else:
                    existing, = db.execute("SELECT payload FROM events WHERE app=? AND id=?",
                                           (app, ident)).fetchone()
                    if existing != payload:
                        raise HTTPException(409, "event ID reused with different content")
        return {"inserted": inserted, "duplicates": len(batch.events) - inserted}

    @api.get("/v1/events", response_model=list[ReceivedEvent])
    def events(app: str = Depends(application), run_id: UUID | None = None,
               trace_id: UUID | None = None, status: Status | None = None,
               limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
        clauses = ["app=?"]
        params = [app]
        for column, value in (("run", run_id), ("trace", trace_id), ("status", status)):
            if value is not None:
                clauses.append(column + "=?")
                params.append(str(value))
        query = "SELECT payload FROM events WHERE " + " AND ".join(clauses)
        with database() as db:
            rows = db.execute(query + " ORDER BY timestamp,id LIMIT ? OFFSET ?",
                              (*params, limit, offset))
            return [json.loads(payload) for payload, in rows]

    return api
