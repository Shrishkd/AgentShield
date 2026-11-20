"""Opt-in end-to-end test against the separate RAG project's installed dependencies."""
import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(not os.getenv("RAG_ROOT"), reason="requires the RAG checkout and local Ollama models")
def test_real_chat_and_failed_retrieval():
    subprocess.run([sys.executable, "-B", "-m", "examples.week2"], check=True, timeout=330)
