# Adapter targets

Advanced Hybrid RAG now has a verified opt-in adapter and local model demo. MONDAY remains at source inspection. See the [RAG runbook](hybrid_rag/README.md).

| Application | Existing boundaries | First integration check |
| --- | --- | --- |
| Advanced Hybrid RAG | `src/serve/api.py` receives requests. `src/graph/rag_graph.py` builds nodes via `make_nodes` and exposes `citation_report`. | Correlate a request with retrieval, generation and citation checks. Exercise a retrieval failure with fixture data and compare application behavior with the collector offline. |
| MONDAY | `monday/pipeline.py` orchestrates work. `monday/agents/resume_tailor.py` contains `check_bullet`, `check_summary` and `_validate`. | Trace a synthetic tailoring case and capture a boolean claim-verification result. Keep resume text, generated bullets and validator messages private. |
