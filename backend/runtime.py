"""Container entry point: validate first and replace PID 1 (no shell expansion)."""

import logging
import os

from dotenv import load_dotenv
from app.services.observability import configure_logging, log_exception
from app.services.runtime_config import server_arguments, validate_runtime


def main():
    configure_logging()
    load_dotenv()
    try:
        validate_runtime()
        arguments = server_arguments()
    except Exception as error:
        log_exception(logging.getLogger(__name__), "runtime_configuration", error)
        return 1
    os.execvp(arguments[0], arguments)


if __name__ == "__main__":
    raise SystemExit(main())
