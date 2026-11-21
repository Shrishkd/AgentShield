"""Replay fixed configuration switches inside a capability-free, fuel-limited Wasm store."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

from pydantic import Field

from agentshield.contracts import Contract, Label

Setting = Literal["citation_retry", "source_fallback"]
Action = Literal["original", "retry", "abstain", "source"]
Tokens = Annotated[list[Label], Field(max_length=8)]
DIRECTORY = Path(__file__).with_name("replay")
FILES = {601: "citation-v1.json", 602: "claim-v1.json"}
RUNTIME = "51.0.0"


class Change(Contract):
    setting: Setting
    before: bool = Field(strict=True)
    after: bool = Field(strict=True)


class Case(Contract):
    name: Label
    kind: Literal["target", "control"]
    allowed: Tokens
    draft: Tokens
    retry: Tokens = Field(default_factory=list)
    expect: Action


class Fixture(Contract):
    version: Literal["1.0"]
    fixture_id: Literal[601, 602]
    setting: Setting
    cases: list[Case] = Field(min_length=2, max_length=16)


class Comparison(Contract):
    name: Label
    kind: Literal["target", "control"]
    expected: Action
    baseline: Action
    candidate: Action


class Result(Contract):
    verdict: Literal["unverified", "passed", "failed"]
    reason: str
    cases: list[Comparison] = Field(default_factory=list)


def bundle(fixture_id):
    fixture_bytes = (DIRECTORY / FILES[fixture_id]).read_text(encoding="utf-8").encode()
    program = (DIRECTORY / "policies.wat").read_text(encoding="utf-8").encode()
    fixture = Fixture.model_validate_json(fixture_bytes)
    if fixture.fixture_id != fixture_id or len({c.name for c in fixture.cases}) != len(fixture.cases):
        raise ValueError("invalid fixture identity")
    worker = (DIRECTORY / "worker.py").read_text(encoding="utf-8").encode()
    digest = hashlib.sha256(b"\0".join((b"v1", RUNTIME.encode(), fixture_bytes, program, worker))).hexdigest()
    return fixture, program, digest


def compare(cases):
    targets = [c for c in cases if c.kind == "target"]
    controls = [c for c in cases if c.kind == "control"]
    if not targets or not controls or any(c.baseline == c.expected for c in targets):
        return Result(verdict="failed", reason="The baseline did not establish the labelled failures.", cases=cases)
    if any(c.baseline != c.expected for c in controls):
        return Result(verdict="failed", reason="A baseline control failed.", cases=cases)
    if any(c.candidate != c.expected for c in cases):
        return Result(verdict="failed", reason="A candidate target or regression control failed.", cases=cases)
    return Result(verdict="passed", reason="All labelled targets improved and all controls passed.", cases=cases)


def inputs(case):
    symbols = sorted(set(case.allowed + case.draft + case.retry))
    def mask(tokens):
        return sum(1 << symbols.index(token) for token in set(tokens))
    return [mask(case.allowed), mask(case.draft), mask(case.retry)]


def verify(fixture_id, expected_digest, change):
    if fixture_id is None or change is None or not expected_digest:
        return Result(verdict="unverified", reason="No matching versioned replay fixture is bound to the evidence.")
    try:
        fixture, program, digest = bundle(fixture_id)
        if digest != expected_digest or change.setting != fixture.setting:
            return Result(verdict="unverified", reason="The approved replay bundle or runtime does not match.")
        request = {"runtime": RUNTIME, "setting": change.setting, "module_digest": hashlib.sha256(program).hexdigest(),
                   "switches": [int(change.before), int(change.after)], "inputs": [inputs(c) for c in fixture.cases]}
        with TemporaryDirectory() as folder:
            worker = subprocess.run([sys.executable, "-I", "-B", str(DIRECTORY / "worker.py")],
                                    cwd=folder, env={k: os.environ[k] for k in ("SYSTEMROOT", "PROCESSOR_ARCHITECTURE")
                                                     if k in os.environ},
                                    input=json.dumps(request), text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=5, check=True)
        actions = ("original", "retry", "abstain", "source")
        rows = []
        for case, (base, candidate) in zip(fixture.cases, json.loads(worker.stdout), strict=True):
            if type(base) is not int or type(candidate) is not int or not (0 <= base < 4 and 0 <= candidate < 4):
                raise ValueError("invalid replay outcome")
            rows.append(Comparison(name=case.name, kind=case.kind, expected=case.expect,
                                   baseline=actions[base], candidate=actions[candidate]))
        return compare(rows)
    except subprocess.TimeoutExpired:
        return Result(verdict="unverified", reason="The isolated replay exceeded its five-second deadline.")
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return Result(verdict="unverified", reason="The restricted replay could not complete.")
