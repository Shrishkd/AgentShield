"""Drop-in entry point for 'streamlit run app.py' from the MailMate checkout."""
import logging
import os
import sys
from contextlib import ExitStack


def main():
    from streamlit.web.cli import main as streamlit

    with ExitStack() as stack:
        key = os.getenv("AGENTSHIELD_APP_KEY")
        if key:
            try:
                from agentshield import Telemetry
                from integrations.mailmate.adapter import instrument

                sdk = Telemetry(os.getenv("AGENTSHIELD_APP_ID", "mailmate"), key,
                                os.getenv("AGENTSHIELD_URL", "http://127.0.0.1:8765"))
                stack.callback(sdk.close)
                stack.enter_context(instrument(sdk))
            except (ImportError, AttributeError, RuntimeError, ValueError):
                logging.getLogger(__name__).warning("MailMate telemetry unavailable")
        sys.argv = ["streamlit", "run", "app.py", *sys.argv[1:]]
        return streamlit()


if __name__ == "__main__":
    sys.exit(main())
