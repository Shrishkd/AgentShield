"""Opt-in ASGI launcher for a dedicated Advanced Hybrid RAG server process."""
import os
import logging
from contextlib import ExitStack
from functools import wraps
from unittest.mock import patch

def instrument(app, telemetry):
    from agentshield.client import SpanOutcome
    from src.generate import citations
    from src.graph import rag_graph
    from src.retrieve.rerank import CrossEncoderReranker

    original_nodes = rag_graph.make_nodes
    original_verify = citations.verify
    original_rerank = CrossEncoderReranker.rerank

    @wraps(original_rerank)
    def rerank(*args, **kwargs):
        with telemetry.span("rerank"):
            return original_rerank(*args, **kwargs)

    def node_span(name, fn):
        @wraps(fn)
        def call(state):
            attributes = {}
            with telemetry.span(name, attributes=attributes, tool=name == "retrieve"):
                result = fn(state)
                if name == "retrieve" and isinstance(result.get("documents"), list):
                    attributes["retrieved_count"] = len(result["documents"])
                return result
        return call

    def make_nodes(*args, **kwargs):
        nodes = original_nodes(*args, **kwargs)
        return {name: node_span(name, fn) for name, fn in nodes.items()}

    def verify(*args, **kwargs):
        with telemetry.span("citation_check"):
            report = original_verify(*args, **kwargs)
            telemetry.record_check("citation_ids", "error" if report.fabricated else "ok",
                                   {"has_citations": bool(report.cited),
                                    "citation_ids_valid": not bool(report.fabricated)})
            return report

    async def observed(scope, receive, send):
        if scope["type"] == "lifespan":
            try:
                with ExitStack() as hooks:
                    hooks.enter_context(patch.object(rag_graph, "make_nodes", make_nodes))
                    hooks.enter_context(patch.object(citations, "verify", verify))
                    hooks.enter_context(patch.object(CrossEncoderReranker, "rerank", rerank))
                    await app(scope, receive, send)
            finally:
                telemetry.close()
        elif scope["type"] == "http" and scope["path"] == "/chat" and scope["method"] == "POST":
            outcome = SpanOutcome()
            async def observe_status(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"x-agentshield-run-id", str(telemetry.current_run_id).encode()))
                    message = {**message, "headers": headers}
                    if message["status"] >= 400:
                        outcome.status = "error"
                        telemetry.record_check("http_response", "error", {})
                await send(message)

            with telemetry.span("chat_request", record_start=True, outcome=outcome):
                await app(scope, receive, observe_status)
        else:
            await app(scope, receive, send)

    return observed


def create_app():
    from src.serve.api import app

    key = os.getenv("AGENTSHIELD_APP_KEY")
    if not key:
        return app
    telemetry = None
    try:
        from agentshield import Telemetry
        telemetry = Telemetry(os.getenv("AGENTSHIELD_APP_ID", "hybrid-rag"), key,
                              os.getenv("AGENTSHIELD_URL", "http://127.0.0.1:8765"))
        return instrument(app, telemetry)
    except (ImportError, AttributeError, RuntimeError, ValueError):
        if telemetry is not None:
            telemetry.close()
        logging.getLogger(__name__).warning("RAG telemetry initialization unavailable")
        return app
