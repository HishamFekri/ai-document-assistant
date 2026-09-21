MAX_PROCESSING_RETRIES = 3
MAX_RETRY_DELAY_SECONDS = 60


def retry_delay(retries: int) -> int:
    return min(
        MAX_RETRY_DELAY_SECONDS,
        2 ** min(max(retries, 0) + 1, 6),
    )
