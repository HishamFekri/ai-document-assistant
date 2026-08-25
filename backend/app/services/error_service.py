import logging


logger = logging.getLogger(__name__)


def log_and_get_public_error(
    error: Exception,
    message: str,
) -> str:
    logger.exception("%s: %s", message, error)
    return message
