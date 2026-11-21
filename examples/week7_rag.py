"""Run the RAG HTTP route and compiled LangGraph with an offline failing retriever."""
import sqlite3
from contextlib import closing
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver
from src.graph.rag_graph import GraphConfig, build_graph
from src.serve import api

from integrations.hybrid_rag.adapter import create_app


def main():
    retriever = Mock()
    retriever.retrieve.side_effect = RuntimeError("synthetic retrieval outage")
    with closing(sqlite3.connect(":memory:", check_same_thread=False)) as connection:
        def offline_startup():
            api._state["graph"] = build_graph(retriever, object(), GraphConfig(), SqliteSaver(connection))

        with patch.object(api.app.router, "on_startup", [offline_startup]), TestClient(create_app()) as client:
            response = client.post("/chat", json={"message": "What is dropout?", "session_id": "week-seven"})
            assert response.status_code == 502
            retriever.retrieve.assert_called_once()


if __name__ == "__main__":
    main()
