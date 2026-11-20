"""Run with the RAG application's Python dependencies. See integrations/hybrid_rag/README.md."""
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

import httpx


def run_requests():
    import numpy as np
    import yaml
    from fastapi.testclient import TestClient
    from integrations.hybrid_rag.adapter import create_app
    from src.embed.ollama_embedder import get_embedder
    from src.retrieve.hybrid import HybridRetriever

    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    if urlparse(host if "://" in host else "http://" + host).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("the demo requires a loopback Ollama endpoint")
    cfg_path = Path("configs/experiment.yaml")
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["generation"].update(model_role="llama3.2:1b", top_n=1)
    cfg["graph"]["nodes"] = []
    cfg["embedding"]["model"] = "embeddinggemma"
    cfg["chunking"]["strategy"] = "recursive"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    question = "What is dropout in a neural network?"
    passage = question + " Dropout randomly disables units during training to reduce overfitting."
    chunk = dict(book="Synthetic ML notes", text=passage, chunk_id="dropout", page_start=0,
                 page_end=0, n_tokens=24)
    corpus = Path("data/chunks/recursive")
    corpus.mkdir(parents=True)
    (corpus / "fixture.jsonl").write_text(json.dumps(chunk), encoding="utf-8")
    vectors = Path("data/processed/vectors")
    vectors.mkdir(parents=True)
    embedder = get_embedder("embeddinggemma")
    # The fixture contains one short, known-length passage.
    np.save(vectors / "embeddinggemma_recursive.npy", embedder._embed([embedder.doc_prefix + passage]))

    headers = {"Authorization": "Bearer " + os.environ["AGENTSHIELD_APP_KEY"]}
    with httpx.Client(base_url=os.environ["AGENTSHIELD_URL"], headers=headers, trust_env=False) as collector:
        with TestClient(create_app()) as rag:
            def ask():
                return rag.post("/chat", json={"session_id": str(uuid4()), "message": question})

            first = ask().raise_for_status()
            success = first.json()
            assert success["raw_answer"] and success["sources"] and not success["blocked"]
            with patch.object(HybridRetriever, "retrieve", side_effect=RuntimeError("fixture failure")):
                second = ask()
                assert second.status_code == 502
        seen = set()
        for response in (first, second):
            run_id = response.headers["x-agentshield-run-id"]
            detail = collector.get("/v1/runs/" + run_id).raise_for_status().json()
            events = detail["events"]
            root, = [e for e in events if e["event_type"] == "span_started"]
            assert all(e["trace_id"] == root["trace_id"] for e in events)
            retrieval, = [e for e in events if e["operation"] == "retrieve"]
            assert retrieval["parent_span_id"] == root["span_id"]
            end, = [e for e in events if e["span_id"] == root["span_id"] and e["event_type"] == "span_finished"]
            assert end["status"] == retrieval["status"]
            ops = {e["operation"] for e in events}
            if retrieval["status"] == "ok":
                assert {"generate", "citation_ids"} <= ops
            else:
                assert "http_response" in ops and "generate" not in ops
            seen.add(retrieval["status"])
            print(run_id, retrieval["status"], len(events), "events")
        assert seen == {"ok", "error"}
        with socket.socket() as unavailable:
            unavailable.bind(("127.0.0.1", 0))
            offline_url = f"http://127.0.0.1:{unavailable.getsockname()[1]}"
            with patch.dict(os.environ, AGENTSHIELD_URL=offline_url), TestClient(create_app()) as rag:
                offline = ask().raise_for_status().json()
                assert offline["raw_answer"] == success["raw_answer"]
                assert offline["sources"] == success["sources"]
        print("Collector offline: cached answer and sources preserved")


if __name__ == "__main__":
    if sys.argv[1:] == ["--child"]:
        run_requests()
    else:
        import secrets
        from contextlib import ExitStack
        from examples.week1 import local_service

        root = Path(os.environ["RAG_ROOT"]).resolve()
        with TemporaryDirectory() as folder, ExitStack() as stack:
            work = Path(folder)
            shutil.copytree(root / "configs", work / "configs")
            env = os.environ.copy()
            if not env.get("AGENTSHIELD_APP_KEY"):
                admin = secrets.token_urlsafe(32)
                url = stack.enter_context(local_service(work / "collector.sqlite3", admin))
                response = httpx.post(url + "/v1/apps", json={"app_id": "hybrid-rag"},
                                      headers={"Authorization": "Bearer " + admin})
                env.update(AGENTSHIELD_APP_KEY=response.raise_for_status().json()["api_key"],
                           AGENTSHIELD_URL=url, AGENTSHIELD_APP_ID="hybrid-rag")
            env.setdefault("AGENTSHIELD_URL", "http://127.0.0.1:8765")
            env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).resolve().parents[1]),
                                                 str(root), env.get("PYTHONPATH", "")])
            subprocess.run([sys.executable, "-B", "-m", "examples.week2", "--child"],
                           env=env, cwd=work, check=True, timeout=300)
