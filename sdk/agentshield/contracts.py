from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

Label = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
Status = Literal["ok", "error"]
Kind = Literal["span_started", "span_finished", "validation", "check"]
FLAGS = {"citation_supported", "citation_ids_valid", "has_citations", "claim_verified",
         "schema_valid", "timed_out", "tool_exception", "citation_required"}
COUNTS = {"retrieved_count", "retry_count"}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Event(Contract):
    schema_version: Literal["1.0"]
    event_id: UUID = Field(default_factory=uuid4)
    app_id: Label
    run_id: UUID
    trace_id: UUID
    span_id: UUID
    parent_span_id: UUID | None = None
    timestamp: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    operation: Label
    event_type: Kind
    status: Status
    duration_ms: float | None = Field(default=None, ge=0, strict=True)
    attributes: dict[str, bool | int] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)

    @field_validator("attributes", mode="before")
    @classmethod
    def sanitize(cls, value):
        if not isinstance(value, dict):
            raise ValueError("attributes must be an object")
        return {
            key: item for key, item in value.items()
            if (key in FLAGS and type(item) is bool)
            or (key in COUNTS and type(item) is int and 0 <= item <= 1_000_000)
        }


class Registration(Contract):
    app_id: Label
