"""Exercise the three sibling applications against one disposable local collector."""
import argparse
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from examples.week1 import local_service

ROOT = Path(__file__).resolve().parents[1]


def run_app(app, url, key, work):
    folder, module, env_name = {
        "hybrid-rag": ("Advance RAG CHATBOT", "examples.week7_rag", "RAG"),
        "monday-demo": ("Monday", "integrations.monday.fixture", "MONDAY"),
        "mailmate": ("MailMate", "examples.week7_mailmate", "MAILMATE"),
    }[app]
    repo = Path(os.getenv(env_name + "_ROOT", ROOT.parent / folder)).resolve()
    venv = repo / ".venv"
    python = os.getenv(env_name + "_PYTHON") or str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    paths = [ROOT, ROOT / "sdk", repo]
    if app == "hybrid-rag":
        shutil.copytree(repo / "configs" / "prompts", work / "configs" / "prompts")
        if os.name == "nt":
            paths.append(venv / "Lib" / "site-packages")
            python = os.getenv("RAG_PYTHON") or shutil.which("python3.11") or python
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(map(str, paths)), "AGENTSHIELD_URL": url,
           "AGENTSHIELD_APP_ID": app, "AGENTSHIELD_APP_KEY": key}
    env.pop("AGENTSHIELD_ADMIN_KEY", None)
    env.pop("AGENTSHIELD_VIEWER_KEY", None)
    subprocess.run([python, "-B", "-m", module], cwd=work, env=env, check=True, timeout=150)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explore", action="store_true")
    args = parser.parse_args()
    admin, viewer = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with TemporaryDirectory() as tmp, patch.dict(os.environ, AGENTSHIELD_VIEWER_KEY=viewer,
            AGENTSHIELD_AUTO_CATEGORIES="", AGENTSHIELD_RANK_MODEL="", AGENTSHIELD_POLICIES=""), local_service(
            Path(tmp) / "demo.sqlite3", admin) as url, httpx.Client(base_url=url, trust_env=False) as client:
        expected = {"hybrid-rag": "retrieve", "monday-demo": "check_bullet", "mailmate": "n8n.create_draft"}
        for app in expected:
            key = client.post("/v1/apps", json={"app_id": app}, headers={"Authorization": "Bearer " + admin}
                              ).raise_for_status().json()["api_key"]
            work = Path(tmp) / app
            work.mkdir()
            run_app(app, url, key, work)
            if app == "mailmate":
                events = client.get("/v1/events", headers={"Authorization": "Bearer " + key}).raise_for_status()
                assert not any(value in events.text for value in (
                    "Synthetic Company", "private fixture", "synthetic-thread", "fixture@example.invalid",
                    "synthetic-token", "gmail.invalid", "workflow.invalid"))
        client.headers["Authorization"] = "Bearer " + viewer
        rows = client.get("/v1/fleet/incidents?limit=100").raise_for_status().json()
        assert {item["app_id"] for item in rows} == set(expected)
        for app, operation in expected.items():
            evidence = []
            for item in (i for i in rows if i["app_id"] == app):
                detail = client.get("/v1/fleet/incidents/" + item["incident_id"],
                                    params={"app_id": app}).raise_for_status().json()
                evidence.extend(e["event"]["operation"] for e in detail["evidence"])
            assert operation in evidence, app
            if app == "mailmate":
                assert {"sentence_verdict", "webhook.save_reply_draft"} <= set(evidence)
            print(f"{app}: confirmed {operation} in fleet incident evidence")
        if args.explore:
            client.get("/explorer/").raise_for_status()
            print(f"Open {url}/explorer/ and select Read-only fleet. Temporary viewer key {viewer}")
            input("Press Enter to stop the collector. ")


if __name__ == "__main__":
    main()
