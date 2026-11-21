import logging
import os
import sys
from contextlib import ExitStack
from pathlib import Path


def main():
    with ExitStack() as hooks:
        key = os.getenv("AGENTSHIELD_APP_KEY")
        if key:
            try:
                from agentshield import Telemetry
                from .adapter import instrument
                sdk = Telemetry(os.getenv("AGENTSHIELD_APP_ID","monday"), key,
                                os.getenv("AGENTSHIELD_URL","http://127.0.0.1:8765"))
                hooks.callback(sdk.close)
                hooks.enter_context(instrument(sdk))
            except (ImportError, AttributeError, RuntimeError, ValueError):
                logging.getLogger(__name__).warning("MONDAY telemetry initialization unavailable")
        if sys.argv[1:2] == ["--streamlit"]:
            import monday
            from streamlit.web import cli
            script = Path(monday.__file__).resolve().parents[1]/"app.py"
            if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.argv = ["streamlit","run",str(script),*sys.argv[2:]]
            return cli.main()
        from monday.cli import main as monday_main
        return monday_main()


if __name__ == "__main__":
    sys.exit(main())
