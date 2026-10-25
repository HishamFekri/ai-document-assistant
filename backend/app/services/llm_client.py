"""Explicit timeout and retry limits for DeepSeek (OpenAI-compatible) clients.

The SDK already retries connection errors, 408/409/429 and 5xx responses with
bounded exponential backoff; this module only makes the limits explicit instead
of inheriting the SDK default of a 600 second timeout with two retries.
"""

import os

import openai


CONNECT_TIMEOUT_SECONDS = 10.0

# purpose: (environment override, default seconds, retries after the first attempt)
CLIENT_PROFILES = {
    "answer": ("LLM_TIMEOUT_SECONDS", 90.0, 2),
    "summary": ("LLM_SUMMARY_TIMEOUT_SECONDS", 120.0, 2),
    "summary_assistant": ("LLM_SUMMARY_ASSISTANT_TIMEOUT_SECONDS", 60.0, 1),
    "title": ("LLM_TITLE_TIMEOUT_SECONDS", 20.0, 1),
    # Intent routing blocks the start of every chat answer and falls back to
    # ordinary chat on failure, so it fails fast instead of retrying.
    "intent": ("LLM_INTENT_TIMEOUT_SECONDS", 15.0, 0),
}


def client_limits(purpose):
    setting, default, retries = CLIENT_PROFILES[purpose]
    try:
        seconds = float(os.getenv(setting, str(default)))
    except ValueError:
        raise ValueError(f"Invalid LLM timeout setting: {setting}") from None
    if not 0 < seconds <= 3600:
        raise ValueError(f"Invalid LLM timeout setting: {setting}")
    # For streamed responses the read timeout applies between chunks.
    timeout = openai.Timeout(seconds, connect=min(CONNECT_TIMEOUT_SECONDS, seconds))
    return timeout, retries


def deepseek_client(purpose, *, api_key, base_url):
    timeout, retries = client_limits(purpose)
    return openai.OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=retries,
    )
