"""Shared environment loading and runtime defaults for the CLI and API."""

import os
from pathlib import Path

CHARS_PER_TOKEN = 4
CONTEXT_LIMIT_TOKENS = 100_000
CONTEXT_LIMIT = CONTEXT_LIMIT_TOKENS * CHARS_PER_TOKEN
MAX_TOOL_OUTPUT = 10_000
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def configured_context_limit():
    """Convert the token budget to our lightweight character estimate."""
    tokens = os.getenv("CONTEXT_LIMIT_TOKENS")
    if tokens is not None:
        return int(tokens) * CHARS_PER_TOKEN
    return int(os.getenv("CONTEXT_LIMIT", str(CONTEXT_LIMIT)))


def estimate_tokens(characters):
    return (characters + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def load_environment():
    # Keep project management usable without optional API dependencies installed.
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ENV_FILE, override=False)


def agent_options():
    """Read the same model-loop settings for either application entry point."""
    return {
        "context_limit": configured_context_limit(),
        "output_limit": int(os.getenv("MAX_TOOL_OUTPUT", str(MAX_TOOL_OUTPUT))),
        "summary_max_tokens": int(os.getenv("SUMMARY_MAX_TOKENS", "4000")),
        "max_tokens": int(os.getenv("MAX_TOKENS", "8000")),
    }
