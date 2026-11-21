"""Install once at process startup, before Streamlit imports MailMate's pages."""
from contextlib import ExitStack, contextmanager
from functools import wraps
from unittest.mock import patch


@contextmanager
def instrument(sdk):
    from mailmate import sentence_check
    from mailmate.n8n import N8nClient

    def observe(fn, operation, *, tool=False, verdict=False):
        @wraps(fn)
        def call(*args, **kwargs):
            with sdk.span(operation, tool=tool):
                result = fn(*args, **kwargs)
                if verdict:
                    sdk.record_check("sentence_verdict", "ok" if result.ok else "error",
                                     {"validator_passed": result.ok})
                return result
        return call

    with ExitStack() as hooks:
        for name in ("personalize", "sync", "write_reply", "save_reply_draft", "send_batch"):
            hooks.enter_context(patch.object(N8nClient, name,
                                observe(getattr(N8nClient, name), "webhook." + name, tool=True)))
        for name in ("check_sentence", "personalize_checked"):
            hooks.enter_context(patch.object(sentence_check, name,
                                observe(getattr(sentence_check, name), name, verdict=name == "check_sentence")))
        yield
