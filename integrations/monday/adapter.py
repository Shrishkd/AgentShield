"""Install process-scoped hooks before starting a MONDAY CLI or Streamlit process."""
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from importlib import import_module
from unittest.mock import patch

from pydantic import ValidationError


@dataclass
class Attempts:
    calls: int = 0
    model_slot: int = 0
    last_model: str | None = None


@contextmanager
def instrument(telemetry):
    import ollama
    from monday import llm, pipeline

    generation = ContextVar("monday_generation", default=None)
    attempts = ContextVar("monday_model_attempts", default=None)

    def boundary(fn, operation, agent=False):
        @wraps(fn)
        def call(*args, **kwargs):
            token = generation.set([0]) if agent else None
            try:
                with telemetry.span(operation, record_start=telemetry.current_run_id is None):
                    result = fn(*args, **kwargs)
                    if operation == "resume_tailor":
                        fallback = any(b.fallback for _, bullets in result.sections for b in bullets)
                        telemetry.record_check("assembled_draft", "ok", {"bullet_fallback_used":fallback,
                                               "summary_fallback_used":result.summary_fallback})
                    elif operation == "job_matcher":
                        report, _ = result
                        passed = not report.dropped_skills
                        telemetry.record_check("job_requirements", "ok" if passed else "error",
                                               {"validator_passed":passed})
                    return result
            finally:
                if token is not None:
                    generation.reset(token)
        return call

    original_chat = ollama.Client.chat
    @wraps(original_chat)
    def chat(*args, **kwargs):
        counter = attempts.get()
        if counter is None:
            return original_chat(*args, **kwargs)
        model = kwargs.get("model")
        if model != counter.last_model:
            counter.model_slot += 1
            counter.last_model = model
        counter.calls += 1
        with telemetry.span("ollama_chat", attributes={"retry_count":counter.calls-1,
                                                      "model_slot":counter.model_slot}):
            return original_chat(*args, **kwargs)

    original_structured = llm.LLM.structured
    @wraps(original_structured)
    def structured(self, agent, system, prompt, schema, *args, **kwargs):
        round_counter = generation.get()
        attrs = {}
        if round_counter is not None:
            round_counter[0] += 1
            attrs["generation_index"] = round_counter[0]
        counter = Attempts()
        token = attempts.set(counter)

        # MONDAY's two schema calls delegate to the original class, including its validators.
        class ObservedSchema:
            model_json_schema = staticmethod(schema.model_json_schema)

            @staticmethod
            def model_validate_json(data):
                try:
                    value = schema.model_validate_json(data)
                except ValidationError:
                    telemetry.record_check("model_schema", "error", {"schema_valid":False})
                    raise
                telemetry.record_check("model_schema", "ok", {"schema_valid":True})
                return value

        try:
            with telemetry.span("structured_generation", attributes=attrs):
                try:
                    return original_structured(self, agent, system, prompt, ObservedSchema, *args, **kwargs)
                except llm.LLMError:
                    attrs["models_exhausted"] = True
                    raise
                finally:
                    if counter.calls:
                        attrs["retry_count"] = counter.calls-1
                        attrs["model_slot"] = counter.model_slot
        finally:
            attempts.reset(token)

    def validator(fn, operation, problems):
        @wraps(fn)
        def check(*args, **kwargs):
            result = fn(*args, **kwargs)
            issues = problems(result)
            attrs = {"validator_passed":not bool(issues)}
            current = generation.get()
            if current is not None:
                attrs["generation_index"] = current[0]
            if operation in {"check_summary","check_bullet"} and any(
                p.startswith(("numbers not in cited facts:", "summary numbers not in cited facts:",
                              "cites facts not in this section:", "cites no valid fact"))
                or (p.startswith("mentions '") and p.endswith("which the cited facts don't support"))
                for p in issues
            ):
                attrs["claim_verified"] = False
            telemetry.record_check(operation, "error" if issues else "ok", attrs)
            return result
        return check

    functions = {"job_matcher": ["run"], "resume_tailor": ["run"], "outreach": ["run"],
                 "cover_letter": ["run"], "interview_prep": ["write_brief","write_prep"],
                 "mock_interview": ["evaluate"]}
    checks = {"resume_tailor": {"check_bullet":list, "check_summary":list, "_validate":list},
              "outreach":{"check_message":lambda r:r.problems},
              "cover_letter":{"check_line":lambda r:r.problems},
              "interview_prep":{"check_brief":lambda r:r[1], "check_prep":lambda r:r.problems},
              "mock_interview":{"check_stronger":list}}
    with ExitStack() as stack:
        def install(module, name, replacement):
            stack.enter_context(patch.object(module, name, replacement))
        for name in ("match","tailor","outreach","cover_letter","interview_prep","answer_mock"):
            install(pipeline, name, boundary(getattr(pipeline,name), "pipeline."+name))
        for name, entries in functions.items():
            module = import_module("monday.agents."+name)
            for entry in entries:
                operation = name if len(entries)==1 else name+"."+entry
                install(module, entry, boundary(getattr(module,entry), operation, agent=True))
            for entry, problems in checks.get(name, {}).items():
                operation = "draft_validation" if entry == "_validate" else entry
                install(module, entry, validator(getattr(module,entry), operation, problems))
        install(llm.LLM, "structured", structured)
        install(ollama.Client, "chat", chat)
        yield
