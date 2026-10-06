"""Shared handling for optional environment settings."""

import os


def env_or(name: str, default: str) -> str:
    """Treat missing, empty, and whitespace-only values as unset."""
    return os.environ.get(name, "").strip() or default
