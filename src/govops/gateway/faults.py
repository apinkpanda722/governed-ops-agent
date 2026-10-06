"""Harness-only model fault scripts; no credentials or transport state."""

import os
import threading
from collections.abc import Collection
from typing import Literal


class ModelFaultScript:
    def __init__(self, failing_calls: Collection[int] | Literal["all"]):
        if failing_calls != "all" and any(
            type(n) is not int or n < 1 for n in failing_calls
        ):
            raise ValueError("Failing calls must be positive integers")
        self._failing = "all" if failing_calls == "all" else frozenset(failing_calls)
        self._calls: dict[str, int] = {}
        self._lock = threading.Lock()

    def should_fail(self, task_id: str) -> bool:
        with self._lock:
            number = self._calls.get(task_id, 0) + 1
            self._calls[task_id] = number
            return self._failing == "all" or number in self._failing


def model_fault_mode() -> str | None:
    mode = os.getenv("GOVOPS_MODEL_FAULT_MODE")
    if mode in (None, ""):
        return None
    if mode != "down_alias":
        raise ValueError("Invalid GOVOPS_MODEL_FAULT_MODE")
    return mode


def model_fault_script(variant: Literal["first", "second", "all"]) -> ModelFaultScript:
    """Create a fresh task-scoped call script for the down_alias backend."""
    if variant == "all":
        return ModelFaultScript("all")
    if variant not in {"first", "second"}:
        raise ValueError("Invalid model fault variant")
    return ModelFaultScript([1 if variant == "first" else 2])


def model_fault_alias(script: ModelFaultScript | None, task_id: str) -> str:
    return (
        "agent-primary-down"
        if script is not None and script.should_fail(task_id)
        else "agent-primary"
    )
