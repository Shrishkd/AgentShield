"""Synthetic child process executed with MONDAY's own dependencies."""
import json
import os
import socket
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
import ollama
import yaml
from agentshield import Telemetry
from monday import pipeline
from monday.config import Config
from monday.db import connect
from monday.llm import LLM, LLMError
from monday.resume_build import BuiltResume
from monday.tracker import Tracker

from .adapter import instrument

FACT = "Built Python services."
UNSUPPORTED = "Built Python services on Kubernetes."
MISSED = "Built Python services in Fortran."
REQUIREMENTS = {"role_title":"Engineer", "seniority":"entry", "min_years_experience":None,
                "must_have_skills":["Python","Kubernetes"], "nice_to_have_skills":[],
                "responsibilities":[], "work_mode":"unknown"}


def draft(text):
    return json.dumps({"summary":"Fixture engineer.", "summary_fact_ids":[], "sections":[
        {"key":"role:0","bullets":[{"fact_ids":["fixture-fact"],"text":text}]}]})


def scenario(directory, sdk, label, replies):
    config = Config(experience_bank=directory/"bank.yaml", agents={
        "job_matcher":["fixture-model"], "resume_tailor":["fixture-model"]})
    if label == "model_switch":
        config.agents["resume_tailor"].append("fixture-alternate")
    sequence = iter([json.dumps(REQUIREMENTS), *replies])

    def respond(request):
        assert request.url.path == "/api/chat"
        reply = next(sequence)
        if reply is None:
            raise httpx.ConnectError("synthetic outage")
        if reply == 404:
            return httpx.Response(404,json={"error":"fixture model unavailable"})
        return httpx.Response(200,json={"model":"fixture-model","message":{"role":"assistant","content":reply}})

    with closing(connect(directory/(label+".db"))) as db:
        tracker = Tracker(db)
        job = tracker.add_job("Fixture Company","Engineer","Synthetic job requiring Python and Kubernetes.")
        client = ollama.Client(host="http://127.0.0.1:1",transport=httpx.MockTransport(respond), trust_env=False)
        model = LLM(config, client=client, sleep=lambda _:None)
        with patch.object(pipeline,"LLM",return_value=model), patch.object(
            pipeline,"build",return_value=BuiltResume(directory/"fixture.tex",None,None,None)
        ), sdk.span("fixture_case"):
            run_id = str(sdk.current_run_id)
            if label == "exhausted":
                try:
                    pipeline.tailor(tracker,config,job)
                except LLMError:
                    assert tracker.latest_draft(job,"resume") is None
                else:
                    raise AssertionError("Expected MONDAY to exhaust its model choices")
            else:
                draft_id, _, _ = pipeline.tailor(tracker,config,job)
                saved = tracker.get_draft(draft_id)
                assert saved["status"] == "pending"
                bullet, = json.loads(saved["content"])["sections"][0]["bullets"]
                assert bullet["text"] == (MISSED if label == "escaped" else FACT)
                assert bullet["fallback"] == (label == "fallback")
                # Independent, fixture-specific labels for unsupported terms in the persisted output.
                escaped = any(term in bullet["text"] for term in ("Kubernetes","Fortran"))
                sdk.record_check("fixture_output_audit", "error" if escaped else "ok",
                                 {"claim_escaped":escaped})
        assert next(sequence, "consumed") == "consumed"
        return run_id


def run():
    sdk = Telemetry("monday-demo",os.environ["AGENTSHIELD_APP_KEY"],os.environ["AGENTSHIELD_URL"])
    cases = {"clean":[draft(FACT)], "corrected":[draft(UNSUPPORTED),draft(FACT)],
             "fallback":[draft(UNSUPPORTED),draft(UNSUPPORTED)], "escaped":[draft(MISSED)],
             "recovered":[None,"bad JSON",draft(FACT)], "model_switch":[404,draft(FACT)],
             "exhausted":["bad JSON","bad JSON"]}
    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        (directory/"bank.yaml").write_text(yaml.safe_dump({
            "profile":{"name":"Synthetic Candidate","email":"fixture@example.invalid","summary":"Fixture engineer."},
            "skills":{"Languages":["Python"]},"roles":[{"company":"Fixture Company","title":"Engineer","start":"2025",
                "facts":[{"id":"fixture-fact","text":FACT,"skills":["Python"]}]}]}), encoding="utf-8")
        try:
            with instrument(sdk):
                runs = {label:scenario(directory,sdk,label,replies) for label,replies in cases.items()}
        finally:
            drained = sdk.close(10)
        assert drained and sdk.dropped == 0
        with socket.socket() as offline:
            offline.bind(("127.0.0.1",0))
            absent = Telemetry("monday-demo","dummy",f"http://127.0.0.1:{offline.getsockname()[1]}")
            try:
                with instrument(absent):
                    scenario(directory,absent,"fallback",cases["fallback"])
            finally:
                assert absent.close(10) and absent.dropped > 0
        return runs


if __name__ == "__main__":
    print(json.dumps(run()))
