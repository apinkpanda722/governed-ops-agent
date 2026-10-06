"""Invoke internal mock administration through compose, without exposing output."""

import json
import subprocess
from pathlib import Path
from typing import Literal, Protocol


class Runner(Protocol):
    def __call__(
        self, command: list[str], *, cwd: Path, input: str | None = None
    ) -> subprocess.CompletedProcess[str]: ...


def subprocess_runner(
    command: list[str], *, cwd: Path, input: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        **({"input": input} if input is not None else {}),
    )


class MockAdminError(RuntimeError):
    def __init__(self, code: Literal["command_failed", "invalid_output"]):
        self.code = code
        super().__init__(code)


class MockAdmin:
    def __init__(self, root: Path, *, runner: Runner = subprocess_runner):
        self.root = root
        self.runner = runner

    def _command(self, service: str, *arguments: str, input: str | None = None) -> dict:
        command = [
            "docker",
            "compose",
            "--env-file",
            ".env",
            "-f",
            "infra/docker-compose.yml",
            "exec",
            "-T",
            service,
            "python",
            "-m",
            "govops.mocks.admin",
            *arguments,
        ]
        # Raise outside the handler so even exception context cannot carry output.
        failed = False
        try:
            result = self.runner(
                command,
                cwd=self.root,
                **({"input": input} if input is not None else {}),
            )
        except (OSError, subprocess.SubprocessError):
            failed = True
        if failed:
            raise MockAdminError("command_failed")
        if result.returncode != 0:
            raise MockAdminError("command_failed")
        invalid = False
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError):
            invalid = True
        if invalid or not isinstance(value, dict):
            raise MockAdminError("invalid_output")
        return value

    def _both(self, *arguments: str) -> dict:
        return {
            name: self._command(f"mock-{name}", *arguments)
            for name in ("jira", "slack")
        }

    def reset(self) -> None:
        self._both("reset")

    def state(self, *, bodies: bool = False) -> dict:
        return self._both("state", *(["--bodies"] if bodies else []))

    def faults_set(self, service: str, rules: list[dict]) -> None:
        if service not in {"mock-jira", "mock-slack"}:
            raise ValueError("Unknown mock service")
        validate_fault_rules(rules)
        self._command(
            service, "faults", "set", "--file", "/dev/stdin", input=json.dumps(rules)
        )

    def faults_clear(self) -> None:
        self._both("faults", "clear")

    def faults_stats(self) -> dict:
        return self._both("faults", "stats")

    def health(self) -> dict[str, bool]:
        health = {}
        for service in ("mock-jira", "mock-slack"):
            try:
                health[service] = self._command(service, "health").get("status") == "ok"
            except MockAdminError:
                health[service] = False
        return health


_ENDPOINTS = frozenset(
    {
        "slack.send",
        "slack.history",
        "slack.channels",
        "jira.comment.create",
        "jira.comment.list",
        "jira.child.create",
        "jira.child.list",
        "jira.ticket.get",
        "jira.ticket.search",
    }
)
_WRITES = frozenset({"slack.send", "jira.comment.create", "jira.child.create"})
_ACTION_FIELDS = {
    "delay": {"when", "ms"},
    "status": {"when", "status"},
    "rate_limit": {"seconds"},
    "drop_after_apply": set(),
    "hide_for": {"ms"},
}


def validate_fault_rules(rules: list[dict]) -> None:
    """Independent CLI contract validation; kept equivalent by cross-layer tests."""
    if not isinstance(rules, list):
        raise ValueError("Rules must be a list")  # noqa: TRY004 — CLI validation uses ValueError
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"endpoint", "nth", "action"}:
            raise ValueError("Invalid rule fields")
        endpoint, nth, action = rule["endpoint"], rule["nth"], rule["action"]
        if not isinstance(endpoint, str) or endpoint not in _ENDPOINTS:
            raise ValueError("Unknown endpoint")
        if nth != "all" and (
            not isinstance(nth, list)
            or not nth
            or any(type(n) is not int or n < 1 for n in nth)
            or len(set(nth)) != len(nth)
        ):
            raise ValueError("Invalid request numbers")
        if not isinstance(action, dict) or set(action) - {
            "type",
            "when",
            "ms",
            "status",
            "seconds",
        }:
            raise ValueError("Invalid action fields")
        kind = action.get("type")
        if not isinstance(kind, str) or kind not in _ACTION_FIELDS:
            raise ValueError("Unknown action")
        present = {
            key
            for key in ("when", "ms", "status", "seconds")
            if action.get(key) is not None
        }
        if present != _ACTION_FIELDS[kind]:
            raise ValueError("Invalid action fields")
        if action.get("when") is not None and (
            not isinstance(action["when"], str)
            or action["when"] not in {"before", "after_apply"}
        ):
            raise ValueError("Invalid action timing")
        for key in ("ms", "seconds", "status"):
            value = action.get(key)
            if value is not None and (
                type(value) is not int
                or value < (500 if key == "status" else 0)
                or (key == "status" and value > 599)
            ):
                raise ValueError("Invalid action value")
        if kind in {"hide_for", "drop_after_apply"} and endpoint not in _WRITES:
            raise ValueError("Action requires a write endpoint")
