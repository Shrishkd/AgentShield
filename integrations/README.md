# Adapter targets

Advanced Hybrid RAG, MONDAY and MailMate have opt-in adapters. The Week 2 RAG demo exercises local inference. MONDAY uses its pipeline and tracker with scripted Ollama responses. MailMate uses its verification and webhook client with in-memory Gmail fixtures. [RAG runbook](hybrid_rag/README.md) · [MONDAY runbook](monday/README.md) · [MailMate and fleet runbook](../docs/week7.md)

| Application | Existing boundaries | First integration check |
| --- | --- | --- |
| Advanced Hybrid RAG | `src/serve/api.py` receives requests. `src/graph/rag_graph.py` builds nodes via `make_nodes` and exposes `citation_report`. | Correlate a request with retrieval, generation and citation checks. Exercise a retrieval failure with fixture data and compare application behavior with the collector offline. |
| MONDAY | `monday/pipeline.py` orchestrates work. `monday/agents/resume_tailor.py` contains `check_bullet`, `check_summary` and `_validate`. | Trace a synthetic tailoring case and capture a boolean claim-verification result. Keep resume text, generated bullets and validator messages private. |
| MailMate | `mailmate.sentence_check` validates personalization and `N8nClient` invokes workflows. Expanded n8n execution logs supply node outcomes. | Reject an empty personalization and capture a failed mock Gmail draft operation. |
