import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows user-account credential storage")
def test_rag_key_save_reload_and_copy(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path)}
    command = r"""
    $ErrorActionPreference = 'Stop'
    function Read-Host { ConvertTo-SecureString $env:TEST_RAG_KEY -AsPlainText -Force }
    function Set-Clipboard { param($Value) $env:TEST_COPIED = $Value }
    $env:TEST_RAG_KEY = 'old-fixture'
    .\scripts\rag-key.ps1 Save
    $env:TEST_RAG_KEY = 'replacement-fixture'
    .\scripts\rag-key.ps1 Save
    .\scripts\rag-key.ps1 Load
    if ($env:AGENTSHIELD_APP_KEY -cne $env:TEST_RAG_KEY -or
        $env:AGENTSHIELD_APP_ID -ne 'hybrid-rag' -or
        $env:AGENTSHIELD_URL -ne 'http://127.0.0.1:8766') { throw 'Load failed' }
    .\scripts\rag-key.ps1 Copy
    if ($env:TEST_COPIED -cne $env:TEST_RAG_KEY) { throw 'Copy failed' }
    """
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=root, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    files = list(tmp_path.rglob("*.dpapi"))
    assert len(files) == 1
    assert "fixture" not in files[0].read_text()
    assert "fixture" not in result.stdout
