import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentshield import Event
from backend.incidents import DEFAULT_POLICY, matches
from examples.week3 import cases


@pytest.mark.parametrize(("attrs","expected"), [
    ({"validator_passed":False}, [("verification_failed","medium","The application validator flagged a draft check.")]),
    ({"validator_passed":False,"claim_verified":False}, [("verification_failed","medium","The verifier rejected a claim.")]),
    ({"claim_escaped":True}, [("escaped_claim","high","An independent check found an unsupported claim in the returned draft.")]),
    ({"validator_passed":True}, []),
    ({"claim_escaped":"true","violations":["synthetic secret"]}, []),
])
def test_reported_outcomes(attrs, expected):
    _, fields = next(cases())
    event = Event.model_validate({**fields,"attributes":attrs,"status":"ok"})
    assert list(matches(event, DEFAULT_POLICY)) == expected
    assert "synthetic secret" not in event.model_dump_json()


def test_real_monday_pipeline():
    root = Path(os.getenv("MONDAY_ROOT", Path(__file__).resolve().parents[2]/"Monday"))
    if not root.is_dir():
        pytest.skip("Set MONDAY_ROOT to run against the separate MONDAY checkout")
    subprocess.run([sys.executable,"-m","examples.week5","--monday-root",str(root)], check=True, timeout=100)
